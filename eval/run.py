"""Runnable evaluation harness producing a scorecard."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from delta_chat.config import config_hash, load_config, load_yaml, project_root
from delta_chat.errors import OcrFailure, PairMismatchError
from delta_chat.pipeline import chat_on_run, run_pair
from eval.budget import analyze as analyze_budget
from eval.budget import to_markdown as budget_markdown
from eval.judges import judge_chat
from eval.matching import match_predictions_to_gt
from eval.metrics import micro_f1


def _git_sha() -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=project_root(), stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001
        return None


def _ocr_available(cfg: dict) -> bool:
    """True when any configured OCR backend can run, not just Tesseract."""
    from delta_chat.ingest.ocr import any_backend_available

    return any_backend_available(cfg)


def _ocr_backend_name(cfg: dict) -> str | None:
    """Which OCR engine the scanned cases actually used, for the scorecard."""
    from delta_chat.ingest.ocr import available_backends

    for name, availability in available_backends(cfg).items():
        if availability.available:
            return name
    return None


def _cad_available() -> bool:
    """True when the DXF reader is installed (DWG additionally needs a converter)."""
    try:
        import ezdxf  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def build_failure_table(
    case_results: list[dict[str, Any]],
    delta_metrics: list[dict[str, Any]],
    chat_scores: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enumerate every way the system underperformed on this run.

    Gate failures alone under-report: a case can clear its threshold while still
    inventing changes or citing the wrong source. This walks the raw per-case
    results so the scorecard states what is actually wrong, not just what
    breached a number we chose.
    """
    failures: list[dict[str, Any]] = []

    for case in case_results:
        status = case.get("status")
        if status not in {"ok", None}:
            failures.append(
                {
                    "kind": "case_status",
                    "case_id": case.get("id"),
                    "detail": status,
                    "error": (case.get("error") or {}).get("message")
                    or (case.get("error") or {}).get("code"),
                }
            )

    for metric in delta_metrics:
        case_id = metric.get("case_id")
        if metric.get("fp"):
            failures.append(
                {
                    "kind": "delta_false_positive",
                    "case_id": case_id,
                    "count": metric["fp"],
                    "detail": f"{metric['fp']} predicted change(s) matched no ground-truth entry",
                    "precision": metric.get("precision"),
                }
            )
        if metric.get("fn"):
            failures.append(
                {
                    "kind": "delta_missed_change",
                    "case_id": case_id,
                    "count": metric["fn"],
                    "detail": f"{metric['fn']} ground-truth change(s) not detected",
                    "recall": metric.get("recall"),
                }
            )

    for score in chat_scores:
        label = f"{score.get('case_id')}/{score.get('qa_id')}"
        if not score.get("fact_ok"):
            failures.append(
                {
                    "kind": "chat_fact_miss",
                    "case_id": label,
                    "detail": "answer did not contain the expected fact",
                }
            )
        if float(score.get("citation_groundedness") or 1.0) < 1.0:
            failures.append(
                {
                    "kind": "citation_not_grounded",
                    "case_id": label,
                    "detail": "cited evidence does not contain the expected value",
                }
            )
        if "refusal_ok" in score and not score.get("refusal_ok"):
            failures.append(
                {
                    "kind": "refusal_miss",
                    "case_id": label,
                    "detail": "expected an unsupported/refusal answer, got a substantive one",
                }
            )

    return failures


def run_eval(dataset_path: str = "eval/datasets/v1.yaml") -> dict[str, Any]:
    root = project_root()
    cfg = load_config()
    eval_cfg = load_yaml("config/eval.yaml")
    ds_path = root / dataset_path if not Path(dataset_path).is_absolute() else Path(dataset_path)
    dataset = yaml.safe_load(ds_path.read_text(encoding="utf-8"))
    run_id = uuid.uuid4().hex[:10]
    out_dir = root / "artifacts" / "eval" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    case_results: list[dict[str, Any]] = []
    delta_metrics: list[dict[str, Any]] = []
    chat_scores: list[dict[str, Any]] = []
    mismatch_correct = 0
    mismatch_total = 0
    retrieval_hits = 0
    retrieval_total = 0
    citation_grounded_vals: list[float] = []
    groundedness_measured_count = 0
    refusal_ok_vals: list[float] = []
    required_failures: list[str] = []
    run_dirs: list[Path] = []
    ocr_ok = _ocr_available(cfg)
    cad_ok = _cad_available()
    require_scanned = bool(eval_cfg.get("gates", {}).get("require_scanned", False))
    require_cad = bool(eval_cfg.get("gates", {}).get("require_cad", False))

    for case in dataset.get("cases", []):
        case_id = case["id"]
        case_out: dict[str, Any] = {
            "id": case_id,
            "status": "ok",
            "required": case.get("required", True),
        }
        requires_ocr = bool(case.get("requires_ocr"))

        if requires_ocr and not ocr_ok:
            case_out["status"] = "skipped_ocr_missing"
            case_out["error"] = {
                "code": "ocr_missing",
                "message": "No OCR backend available (tried rapidocr, tesseract)",
            }
            if case.get("required", True) and require_scanned:
                required_failures.append(f"{case_id}: required scanned case skipped (OCR missing)")
            case_results.append(case_out)
            (out_dir / f"{case_id}.json").write_text(
                json.dumps(case_out, indent=2, default=str), encoding="utf-8"
            )
            continue

        if bool(case.get("requires_cad")) and not cad_ok:
            case_out["status"] = "skipped_cad_missing"
            case_out["error"] = {
                "code": "cad_missing",
                "message": "ezdxf not installed",
            }
            if case.get("required", True) and require_cad:
                required_failures.append(f"{case_id}: required CAD case skipped (ezdxf missing)")
            case_results.append(case_out)
            (out_dir / f"{case_id}.json").write_text(
                json.dumps(case_out, indent=2, default=str), encoding="utf-8"
            )
            continue

        try:
            mode = case.get("mismatch_mode", "warn")
            result = run_pair(
                case["pid_a"],
                case["pid_b"],
                config=cfg,
                mismatch_mode=mode,
                request_id=f"eval-{case_id}-{run_id[:6]}"[:64],
            )
            delta = result["delta"]
            run_dirs.append(Path(result["run_dir"]))
            case_out["request_id"] = result["request_id"]
            case_out["run_dir"] = result["run_dir"]
            case_out["changes"] = delta["summary"].get("total_changes")
            case_out["compatible"] = delta["pair_compatibility"].get("compatible")

            if case.get("expect_mismatch"):
                mismatch_total += 1
                is_mismatch = not bool(delta["pair_compatibility"].get("compatible"))
                if is_mismatch:
                    mismatch_correct += 1
                case_out["mismatch_detected"] = is_mismatch
                if not is_mismatch and case.get("required", True):
                    required_failures.append(f"{case_id}: expected mismatch not detected")
            else:
                gt_path = case.get("ground_truth_path")
                if gt_path:
                    gt_file = root / gt_path
                    if gt_file.exists():
                        gt = json.loads(gt_file.read_text(encoding="utf-8"))
                        changes_gt = gt.get("controlled_changes") or []
                        m = match_predictions_to_gt(
                            delta.get("changes") or [],
                            changes_gt,
                            centroid_tol=float(eval_cfg.get("centroid_tol", 0.08)),
                            iou_thr=float(eval_cfg.get("location_iou_threshold", 0.05)),
                        )
                        m["case_id"] = case_id
                        delta_metrics.append(m)
                        case_out["delta_metrics"] = {
                            k: m[k] for k in ("precision", "recall", "f1", "tp", "fp", "fn")
                        }

            # retrieval with family/text expectations
            from delta_chat.retrieval.hybrid import HybridRetriever

            retriever = HybridRetriever(result["records"], cfg)
            for rq in case.get("retrieval") or []:
                retrieval_total += 1
                hits = retriever.search(rq["query"], top_k=5)
                must = (rq.get("must_match_text") or rq["query"]).upper().replace(" ", "")
                ok = False
                for h in hits:
                    rec = h.get("record") or {}
                    blob = (
                        ((rec.get("text") or "") + " " + " ".join(rec.get("identifiers") or []))
                        .upper()
                        .replace(" ", "")
                    )
                    fams = rq.get("expect_families")
                    fam_ok = (not fams) or (rec.get("source_family") in fams)
                    if must.replace("-", "") in blob.replace("-", "") and fam_ok:
                        ok = True
                        break
                if ok:
                    retrieval_hits += 1
                case_out.setdefault("retrieval", []).append(
                    {
                        "query": rq["query"],
                        "hit": ok,
                        "top": [h.get("source_id") for h in hits[:3]],
                    }
                )

            for qa in case.get("qa") or []:
                ans = chat_on_run(result, qa["question"])
                j = judge_chat(ans, qa)
                j["case_id"] = case_id
                j["qa_id"] = qa["id"]
                chat_scores.append(j)
                citation_grounded_vals.append(float(j.get("citation_groundedness") or 0.0))
                if j.get("groundedness_measured"):
                    groundedness_measured_count += 1
                if "unsupported" in qa:
                    refusal_ok_vals.append(1.0 if j["refusal_ok"] else 0.0)
                case_out.setdefault("qa", []).append({"id": qa["id"], "answer": ans, "judge": j})

        except OcrFailure as exc:
            case_out["status"] = "error_ocr"
            case_out["error"] = exc.to_dict()
            if case.get("required", True):
                required_failures.append(f"{case_id}: OCR failure")
        except PairMismatchError as exc:
            case_out["status"] = "pair_mismatch_error"
            case_out["error"] = exc.to_dict()
            if case.get("expect_mismatch"):
                mismatch_total += 1
                mismatch_correct += 1
                case_out["mismatch_detected"] = True
            elif case.get("required", True):
                required_failures.append(f"{case_id}: unexpected pair mismatch error")
        except Exception as exc:  # noqa: BLE001
            case_out["status"] = "error"
            case_out["error"] = {"type": type(exc).__name__, "message": str(exc)}
            if case.get("required", True):
                required_failures.append(f"{case_id}: {type(exc).__name__}: {exc}")

        case_results.append(case_out)
        (out_dir / f"{case_id}.json").write_text(
            json.dumps(case_out, indent=2, default=str), encoding="utf-8"
        )

    micro = micro_f1(delta_metrics) if delta_metrics else {"precision": 0, "recall": 0, "f1": 0}
    native = next((m for m in delta_metrics if m.get("case_id") == "native_revision"), None)
    scanned = next((m for m in delta_metrics if m.get("case_id") == "scanned_revision"), None)
    cad = next((m for m in delta_metrics if m.get("case_id") == "cad_revision"), None)
    native_f1 = native["f1"] if native else None
    scanned_f1 = scanned["f1"] if scanned else None
    cad_f1 = cad["f1"] if cad else None

    chat_fact = sum(1 for c in chat_scores if c.get("fact_ok")) / max(1, len(chat_scores))
    cite_prec = sum(c.get("citation_precision", 1) for c in chat_scores) / max(1, len(chat_scores))
    cite_val = (
        sum(citation_grounded_vals) / max(1, len(citation_grounded_vals))
        if citation_grounded_vals
        else 0.0
    )
    refusal_acc = sum(refusal_ok_vals) / max(1, len(refusal_ok_vals)) if refusal_ok_vals else 1.0
    mismatch_acc = (mismatch_correct / mismatch_total) if mismatch_total else 1.0
    retrieval_r5 = (retrieval_hits / retrieval_total) if retrieval_total else None

    gates = eval_cfg.get("gates", {})
    gate_results: dict[str, bool] = {}
    if native_f1 is not None:
        gate_results["native_delta_f1"] = native_f1 >= float(gates.get("native_delta_f1", 0.7))
    else:
        gate_results["native_delta_f1"] = False
        required_failures.append("native_delta_f1 missing")

    if scanned_f1 is not None:
        gate_results["scanned_delta_f1"] = scanned_f1 >= float(gates.get("scanned_delta_f1", 0.45))
    elif require_scanned:
        gate_results["scanned_delta_f1"] = False
        required_failures.append("scanned_delta_f1 missing (OCR required)")
    else:
        gate_results["scanned_delta_f1"] = True  # not required in this environment

    if cad_f1 is not None:
        gate_results["cad_delta_f1"] = cad_f1 >= float(gates.get("cad_delta_f1", 0.8))
    elif require_cad:
        gate_results["cad_delta_f1"] = False
        required_failures.append("cad_delta_f1 missing (CAD required)")
    else:
        gate_results["cad_delta_f1"] = True

    gate_results["pair_mismatch_accuracy"] = mismatch_acc >= float(
        gates.get("pair_mismatch_accuracy", 1.0)
    )
    gate_results["citation_groundedness"] = cite_val >= float(
        gates.get("citation_groundedness", 1.0)
    )
    gate_results["unsupported_refusal"] = refusal_acc >= float(
        gates.get("unsupported_refusal", 1.0)
    )
    gate_results["no_required_case_failures"] = len(required_failures) == 0

    summary = {
        "run_id": run_id,
        "seed": dataset.get("seed", eval_cfg.get("seed")),
        "git_sha": _git_sha(),
        "config_hash": config_hash(cfg),
        # Fingerprint of the questions themselves. Scores are only comparable
        # across runs that asked the same things; without this a harder dataset
        # is indistinguishable from a worse system.
        "dataset_hash": hashlib.sha256(
            ds_path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()[:16],
        "ocr_available": ocr_ok,
        "ocr_backend": _ocr_backend_name(cfg),
        "cad_available": cad_ok,
        "native_delta_f1": native_f1,
        "scanned_delta_f1": scanned_f1,
        "cad_delta_f1": cad_f1,
        "delta_micro": micro,
        "pair_mismatch_accuracy": round(mismatch_acc, 4),
        "retrieval_recall_at_5": retrieval_r5,
        "chat_fact_accuracy": round(chat_fact, 4),
        "citation_precision": round(cite_prec, 4),
        "citation_groundedness": round(cite_val, 4),
        "citation_groundedness_n_measured": groundedness_measured_count,
        "chat_qa_total": len(chat_scores),
        "unsupported_refusal_accuracy": round(refusal_acc, 4),
        "required_failures": required_failures,
        "cases": [
            {
                "id": c["id"],
                "status": c["status"],
                "required": c.get("required"),
                "compatible": c.get("compatible"),
                "delta_metrics": c.get("delta_metrics"),
            }
            for c in case_results
        ],
        "generated_at": time.time(),
        "gates": gate_results,
        "all_gates_passed": all(gate_results.values()),
    }

    # Budget analysis reads the metrics.json each pair run already wrote, so it
    # measures the same execution the quality numbers came from.
    budget = analyze_budget(run_dirs, eval_cfg)
    summary["budget"] = budget

    failure_table = build_failure_table(case_results, delta_metrics, chat_scores)
    summary["known_failures"] = len(failure_table)

    scorecard = {
        "summary": summary,
        "case_results": case_results,
        "delta_metrics": delta_metrics,
        "budget": budget,
        "failure_table": failure_table,
        "required_failures": required_failures,
    }
    (out_dir / "scorecard.json").write_text(
        json.dumps(scorecard, indent=2, default=str), encoding="utf-8"
    )
    md = [
        f"# Eval scorecard `{run_id}`",
        "",
        f"- Git: `{summary['git_sha']}`",
        f"- Config hash: `{summary['config_hash']}`",
        f"- OCR available: **{ocr_ok}** (backend: `{summary['ocr_backend']}`)",
        f"- CAD available: **{cad_ok}**",
        f"- Native delta F1: **{native_f1}**",
        f"- Scanned delta F1: **{scanned_f1}**",
        f"- CAD delta F1: **{cad_f1}**",
        f"- Pair mismatch accuracy: **{mismatch_acc}**",
        f"- Citation groundedness: **{cite_val}** (asserted on {groundedness_measured_count}/{len(chat_scores)} Q&A)",
        f"- Unsupported refusal: **{refusal_acc}**",
        f"- Chat fact accuracy: **{chat_fact}**",
        f"- Retrieval R@5: **{retrieval_r5}**",
        f"- All gates passed: **{summary['all_gates_passed']}**",
        "",
        "## Required failures",
        "",
    ]
    if required_failures:
        md.extend(f"- {f}" for f in required_failures)
    else:
        md.append("_None_")
    md += [
        "",
        "## Gates",
        "",
        "```json",
        json.dumps(gate_results, indent=2),
        "```",
        "",
        "## Cases",
        "",
    ]
    for c in summary["cases"]:
        md.append(f"- `{c['id']}` status={c['status']} metrics={c.get('delta_metrics')}")

    md += ["", "## Failure table", ""]
    if failure_table:
        md.append("| kind | case | detail |")
        md.append("|---|---|---|")
        for f in failure_table:
            md.append(f"| `{f['kind']}` | `{f.get('case_id')}` | {f.get('detail')} |")
    else:
        md.append("_No failures recorded on this run._")

    md += ["", *budget_markdown(budget)]
    (out_dir / "scorecard.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # Stable path so `make eval-compare` and CI never hunt for a random run id.
    latest_path = out_dir.parent / "latest.json"
    latest_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")

    print("=== EVAL SCORECARD ===")
    print(json.dumps(summary, indent=2, default=str))
    if failure_table:
        print(f"\n--- FAILURE TABLE ({len(failure_table)}) ---")
        for f in failure_table:
            print(f"  [{f['kind']}] {f.get('case_id')}: {f.get('detail')}")
    print(f"\nWrote {out_dir / 'scorecard.json'}")
    print(f"Wrote {latest_path}")

    fail_on_gate = bool(gates.get("fail_on_gate", True))
    if fail_on_gate and not summary["all_gates_passed"]:
        # non-zero exit for make eval / CI
        print("EVAL GATES FAILED", file=sys.stderr)
        sys.exit(1)
    return scorecard


if __name__ == "__main__":
    run_eval()
