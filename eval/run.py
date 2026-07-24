"""Runnable evaluation harness producing a scorecard."""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from delta_chat.config import load_config, load_yaml, project_root
from delta_chat.errors import OcrFailure, PairMismatchError
from delta_chat.pipeline import chat_on_run, run_pair
from eval.judges import judge_chat
from eval.matching import match_predictions_to_gt
from eval.metrics import micro_f1


def _git_sha() -> str | None:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project_root(), stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001
        return None


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
    citation_validity = []
    refusal_ok = []

    for case in dataset.get("cases", []):
        case_id = case["id"]
        case_out = {"id": case_id, "status": "ok"}
        try:
            mode = case.get("mismatch_mode", "warn")
            if case.get("expect_mismatch") and mode == "strict":
                # still run warn to produce artifacts; strict tested separately
                pass
            result = run_pair(
                case["pid_a"],
                case["pid_b"],
                config=cfg,
                mismatch_mode=mode,
                request_id=f"eval-{case_id}-{run_id[:6]}",
            )
            delta = result["delta"]
            case_out["request_id"] = result["request_id"]
            case_out["changes"] = delta["summary"].get("total_changes")
            case_out["compatible"] = delta["pair_compatibility"].get("compatible")

            if case.get("expect_mismatch"):
                mismatch_total += 1
                is_mismatch = not bool(delta["pair_compatibility"].get("compatible"))
                if is_mismatch:
                    mismatch_correct += 1
                case_out["mismatch_detected"] = is_mismatch
            else:
                # delta quality
                gt_path = case.get("ground_truth_path")
                if gt_path:
                    gt_file = root / gt_path
                    if gt_file.exists():
                        gt = json.loads(gt_file.read_text(encoding="utf-8"))
                        changes_gt = gt.get("controlled_changes") or []
                        # ignore pure geometry if needed soft
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

            # retrieval
            for rq in case.get("retrieval") or []:
                retrieval_total += 1
                from delta_chat.retrieval.hybrid import HybridRetriever

                retriever = HybridRetriever(result["records"], cfg)
                hits = retriever.search(rq["query"], top_k=5)
                top_ids = [h["source_id"] for h in hits]
                ok = any(rq["query"].replace(" ", "").upper() in (h.get("record", {}).get("text", "").upper() + " ".join(h.get("record", {}).get("identifiers") or [])) for h in hits)
                if ok or (hits and rq.get("expect_recall_at_1")):
                    # check query tag in top1 text/ids
                    blob = json.dumps(hits[0] if hits else {})
                    if rq["query"].split("-")[-1] in blob or rq["query"] in blob:
                        retrieval_hits += 1
                        ok = True
                case_out.setdefault("retrieval", []).append({"query": rq["query"], "hit": ok, "top": top_ids[:3]})

            # chat
            for qa in case.get("qa") or []:
                ans = chat_on_run(result, qa["question"])
                j = judge_chat(ans, qa)
                j["case_id"] = case_id
                j["qa_id"] = qa["id"]
                chat_scores.append(j)
                citation_validity.append(j["citation_validity"])
                if "unsupported" in qa:
                    refusal_ok.append(1.0 if j["refusal_ok"] else 0.0)
                case_out.setdefault("qa", []).append({"id": qa["id"], "answer": ans, "judge": j})

        except OcrFailure as exc:
            if case.get("optional_if_ocr_missing"):
                case_out["status"] = "skipped_ocr_missing"
                case_out["error"] = exc.to_dict()
            else:
                case_out["status"] = "error"
                case_out["error"] = exc.to_dict()
        except PairMismatchError as exc:
            case_out["status"] = "pair_mismatch_error"
            case_out["error"] = exc.to_dict()
            if case.get("expect_mismatch"):
                mismatch_total += 1
                mismatch_correct += 1
                case_out["mismatch_detected"] = True
        except Exception as exc:  # noqa: BLE001
            case_out["status"] = "error"
            case_out["error"] = {"type": type(exc).__name__, "message": str(exc)}

        case_results.append(case_out)
        (out_dir / f"{case_id}.json").write_text(json.dumps(case_out, indent=2, default=str), encoding="utf-8")

    micro = micro_f1(delta_metrics) if delta_metrics else {"precision": 0, "recall": 0, "f1": 0}
    # split native/scanned if present
    native = [m for m in delta_metrics if m.get("case_id") == "native_revision"]
    scanned = [m for m in delta_metrics if m.get("case_id") == "scanned_revision"]
    native_f1 = native[0]["f1"] if native else None
    scanned_f1 = scanned[0]["f1"] if scanned else None

    chat_fact = sum(1 for c in chat_scores if c.get("fact_ok")) / max(1, len(chat_scores))
    cite_prec = sum(c.get("citation_precision", 1) for c in chat_scores) / max(1, len(chat_scores))
    cite_val = sum(citation_validity) / max(1, len(citation_validity)) if citation_validity else 1.0
    refusal_acc = sum(refusal_ok) / max(1, len(refusal_ok)) if refusal_ok else 1.0
    mismatch_acc = (mismatch_correct / mismatch_total) if mismatch_total else 1.0
    retrieval_r5 = (retrieval_hits / retrieval_total) if retrieval_total else None

    summary = {
        "run_id": run_id,
        "seed": dataset.get("seed", eval_cfg.get("seed")),
        "git_sha": _git_sha(),
        "config_hash": None,
        "native_delta_f1": native_f1,
        "scanned_delta_f1": scanned_f1,
        "delta_micro": micro,
        "pair_mismatch_accuracy": round(mismatch_acc, 4),
        "retrieval_recall_at_5": retrieval_r5,
        "chat_fact_accuracy": round(chat_fact, 4),
        "citation_precision": round(cite_prec, 4),
        "citation_validity": round(cite_val, 4),
        "unsupported_refusal_accuracy": round(refusal_acc, 4),
        "cases": [
            {
                "id": c["id"],
                "status": c["status"],
                "compatible": c.get("compatible"),
                "delta_metrics": c.get("delta_metrics"),
            }
            for c in case_results
        ],
        "generated_at": time.time(),
    }
    from delta_chat.config import config_hash

    summary["config_hash"] = config_hash(cfg)

    gates = eval_cfg.get("gates", {})
    gate_results = {}
    if native_f1 is not None:
        gate_results["native_delta_f1"] = native_f1 >= float(gates.get("native_delta_f1", 0.7))
    if scanned_f1 is not None:
        gate_results["scanned_delta_f1"] = scanned_f1 >= float(gates.get("scanned_delta_f1", 0.45))
    gate_results["pair_mismatch_accuracy"] = mismatch_acc >= float(gates.get("pair_mismatch_accuracy", 1.0))
    gate_results["citation_validity"] = cite_val >= float(gates.get("citation_validity", 1.0))
    gate_results["unsupported_refusal"] = refusal_acc >= float(gates.get("unsupported_refusal", 1.0))
    summary["gates"] = gate_results

    scorecard = {"summary": summary, "case_results": case_results, "delta_metrics": delta_metrics}
    (out_dir / "scorecard.json").write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")

    md = [
        f"# Eval scorecard `{run_id}`",
        "",
        f"- Git: `{summary['git_sha']}`",
        f"- Config hash: `{summary['config_hash']}`",
        f"- Native delta F1: **{native_f1}**",
        f"- Scanned delta F1: **{scanned_f1}**",
        f"- Pair mismatch accuracy: **{mismatch_acc}**",
        f"- Citation validity: **{cite_val}**",
        f"- Unsupported refusal: **{refusal_acc}**",
        f"- Chat fact accuracy: **{chat_fact}**",
        f"- Retrieval R@5: **{retrieval_r5}**",
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
    (out_dir / "scorecard.md").write_text("\n".join(md), encoding="utf-8")

    # print human table
    print("=== EVAL SCORECARD ===")
    print(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {out_dir / 'scorecard.json'}")
    return scorecard


if __name__ == "__main__":
    run_eval()
