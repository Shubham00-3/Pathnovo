"""Compare two eval scorecards and report regressions.

The brief asks that results be comparable across runs so a change can be shown
to help or hurt. `eval/baseline.json` is the committed reference; `make eval`
writes `artifacts/eval/latest.json` on every pass, and `make eval-compare` diffs
the two and exits non-zero when a tracked metric drops by more than its
tolerance.

Direction matters: F1 going up is an improvement, latency going up is not, so
each metric declares which way is better.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from delta_chat.config import project_root

# metric name -> (higher_is_better, absolute tolerance before it counts)
TRACKED_METRICS: dict[str, tuple[bool, float]] = {
    "native_delta_f1": (True, 0.02),
    "scanned_delta_f1": (True, 0.05),
    "cad_delta_f1": (True, 0.02),
    "pair_mismatch_accuracy": (True, 0.0),
    "citation_groundedness": (True, 0.0),
    "unsupported_refusal_accuracy": (True, 0.0),
    "chat_fact_accuracy": (True, 0.05),
    "retrieval_recall_at_5": (True, 0.05),
    "citation_precision": (True, 0.05),
}

# Tolerance is generous because these are wall-clock timings on a dev machine,
# not a controlled benchmark. It catches an order-of-magnitude slowdown.
LATENCY_TOLERANCE_RATIO = 1.5


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Scorecard not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    # Accept either a full scorecard or a bare summary.
    return data.get("summary", data)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []

    # Scores only mean the same thing when the questions are the same. Adding
    # harder cases legitimately lowers a score without the system getting worse,
    # and reporting that as a regression trains people to ignore the tool. When
    # the dataset differs the numbers are still shown, but as information rather
    # than a verdict.
    base_dataset = baseline.get("dataset_hash")
    cur_dataset = current.get("dataset_hash")
    dataset_changed = bool(base_dataset and cur_dataset and base_dataset != cur_dataset)

    for metric, (higher_is_better, tolerance) in TRACKED_METRICS.items():
        base_value = _as_float(baseline.get(metric))
        cur_value = _as_float(current.get(metric))
        row: dict[str, Any] = {
            "metric": metric,
            "baseline": base_value,
            "current": cur_value,
        }

        if base_value is None and cur_value is None:
            row["status"] = "absent"
        elif base_value is None:
            row["status"] = "new"
        elif cur_value is None:
            # A metric that used to exist and now does not is a regression: it
            # usually means a case stopped running rather than started passing.
            row["status"] = "missing"
            regressions.append(row)
        else:
            delta = cur_value - base_value
            row["delta"] = round(delta, 4)
            signed = delta if higher_is_better else -delta
            if signed < -tolerance:
                row["status"] = "regressed"
                regressions.append(row)
            elif signed > tolerance:
                row["status"] = "improved"
                improvements.append(row)
            else:
                row["status"] = "unchanged"
        rows.append(row)

    # Gates that were passing and now are not.
    base_gates = baseline.get("gates") or {}
    cur_gates = current.get("gates") or {}
    gate_regressions = [
        name for name, passed in cur_gates.items() if base_gates.get(name) and not passed
    ]
    for name in gate_regressions:
        regressions.append({"metric": f"gate:{name}", "status": "gate_failed"})

    base_latency = _as_float(((baseline.get("budget") or {}).get("end_to_end") or {}).get("p95_ms"))
    cur_latency = _as_float(((current.get("budget") or {}).get("end_to_end") or {}).get("p95_ms"))
    latency_row = None
    if base_latency and cur_latency:
        latency_row = {
            "metric": "end_to_end_p95_ms",
            "baseline": base_latency,
            "current": cur_latency,
            "ratio": round(cur_latency / base_latency, 2),
        }
        if cur_latency > base_latency * LATENCY_TOLERANCE_RATIO:
            latency_row["status"] = "regressed"
            regressions.append(latency_row)
        else:
            latency_row["status"] = "ok"

    return {
        "baseline_run_id": baseline.get("run_id"),
        "current_run_id": current.get("run_id"),
        "baseline_git_sha": baseline.get("git_sha"),
        "current_git_sha": current.get("git_sha"),
        "baseline_dataset_hash": base_dataset,
        "current_dataset_hash": cur_dataset,
        "dataset_changed": dataset_changed,
        "rows": rows,
        "latency": latency_row,
        "improvements": improvements,
        "regressions": regressions,
        # Differences are real either way, but only attributable to the system
        # when both runs answered the same questions.
        "has_regression": bool(regressions) and not dataset_changed,
        "regressions_not_attributable": bool(regressions) and dataset_changed,
    }


def render(result: dict[str, Any]) -> str:
    # ASCII only: this prints to a Windows console under cp1252 in the default
    # dev setup, where arrow glyphs raise UnicodeEncodeError.
    arrow = {
        "improved": "UP",
        "regressed": "DOWN",
        "unchanged": "--",
        "new": "NEW",
        "missing": "!!",
    }
    lines = [
        "=== EVAL COMPARISON ===",
        f"baseline: {result['baseline_run_id']} ({str(result['baseline_git_sha'])[:8]})",
        f"current : {result['current_run_id']} ({str(result['current_git_sha'])[:8]})",
        "",
        f"{'metric':<32}{'baseline':>10}{'current':>10}{'delta':>10}  status",
    ]
    for row in result["rows"]:
        if row["status"] == "absent":
            continue
        # ASCII placeholders: this prints to a cp1252 console where an em dash
        # renders as a replacement character.
        base = "n/a" if row.get("baseline") is None else f"{row['baseline']:.4f}"
        cur = "n/a" if row.get("current") is None else f"{row['current']:.4f}"
        delta = "n/a" if row.get("delta") is None else f"{row['delta']:+.4f}"
        mark = arrow.get(row["status"], " ")
        lines.append(f"{row['metric']:<32}{base:>10}{cur:>10}{delta:>10}  {mark} {row['status']}")

    if result.get("latency"):
        lat = result["latency"]
        lines.append(
            f"{'end_to_end_p95_ms':<32}{lat['baseline']:>10.0f}{lat['current']:>10.0f}"
            f"{'x' + str(lat['ratio']):>10}  {lat['status']}"
        )

    lines.append("")
    if result.get("dataset_changed"):
        lines += [
            "NOTE: the eval dataset changed between these runs",
            f"      baseline dataset {result.get('baseline_dataset_hash')}",
            f"      current  dataset {result.get('current_dataset_hash')}",
            "      Scores are not directly comparable. Differences below may",
            "      reflect harder questions rather than a worse system.",
            "      Re-baseline deliberately once the new dataset is accepted.",
            "",
        ]

    if result["has_regression"]:
        lines.append(f"REGRESSIONS: {len(result['regressions'])}")
        for row in result["regressions"]:
            lines.append(f"  - {row['metric']} ({row['status']})")
    elif result.get("regressions_not_attributable"):
        lines.append(
            f"Differences (not attributable, dataset changed): {len(result['regressions'])}"
        )
        for row in result["regressions"]:
            lines.append(f"  - {row['metric']} ({row['status']})")
    else:
        lines.append("No regressions against baseline.")
    if result["improvements"]:
        lines.append(f"Improvements: {', '.join(r['metric'] for r in result['improvements'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    root = project_root()
    parser = argparse.ArgumentParser(description="Compare an eval scorecard against a baseline.")
    parser.add_argument("--baseline", default=str(root / "eval" / "baseline.json"))
    parser.add_argument("--current", default=str(root / "artifacts" / "eval" / "latest.json"))
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite the baseline with the current scorecard and exit.",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        default=True,
        help="Exit non-zero when a tracked metric regresses (default).",
    )
    parser.add_argument("--no-fail-on-regression", dest="fail_on_regression", action="store_false")
    args = parser.parse_args(argv)

    current_path = Path(args.current)
    baseline_path = Path(args.baseline)

    if args.update_baseline:
        current = _load(current_path)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(current, indent=2, default=str) + "\n", encoding="utf-8"
        )
        print(f"Baseline updated from {current_path} -> {baseline_path}")
        return 0

    result = compare(_load(baseline_path), _load(current_path))
    print(render(result))

    out_path = current_path.parent / "comparison.json"
    out_path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nWrote {out_path}")

    if result["has_regression"] and args.fail_on_regression:
        print("EVAL REGRESSION DETECTED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
