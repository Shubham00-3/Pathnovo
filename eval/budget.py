"""Cost and latency budget analysis across an eval run.

A scorecard that reports only quality says nothing about whether the system is
affordable or fast enough to ship. This aggregates the per-stage timings and LLM
telemetry that every run already writes, compares them against declared budgets
in config/eval.yaml, and reports overruns as first-class findings.

Percentiles come from a handful of eval cases, so they are indicative, not a
load-test result -- `sample_size` is reported alongside every figure so nobody
reads more into them than is there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Stages worth a budget. Everything else is rolled into the end-to-end total.
TRACKED_STAGES = (
    "ingest.a",
    "ingest.b",
    "delta.engine",
    "report.write",
    "markup.overlay",
    "retrieval.index",
    "chat.chat.request",
)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; exact and well-defined for tiny samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(pct / 100.0 * len(ordered) + 0.5))))
    return round(ordered[rank - 1], 2)


def collect_run_metrics(run_dirs: list[Path]) -> list[dict[str, Any]]:
    """Read metrics.json from each pair run that the eval executed."""
    collected: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        metrics_path = Path(run_dir) / "metrics.json"
        if not metrics_path.exists():
            continue
        try:
            collected.append(json.loads(metrics_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return collected


def analyze(run_dirs: list[Path], eval_cfg: dict) -> dict[str, Any]:
    """Build the budget report for the runs an eval pass produced."""
    metrics = collect_run_metrics(run_dirs)
    budgets = (eval_cfg or {}).get("budgets", {}) or {}

    stage_samples: dict[str, list[float]] = {}
    totals: list[float] = []
    llm_calls = 0
    llm_tokens = 0
    llm_cost: float | None = None
    cost_reasons: set[str] = set()

    for entry in metrics:
        stages = entry.get("stage_latency_ms") or {}
        for name, value in stages.items():
            try:
                stage_samples.setdefault(name, []).append(float(value))
            except (TypeError, ValueError):
                continue
        if stages:
            totals.append(sum(float(v) for v in stages.values() if _is_number(v)))
        llm = entry.get("llm") or {}
        llm_calls += int(llm.get("calls") or 0)
        llm_tokens += int(llm.get("total_tokens") or 0)
        if llm.get("total_cost") is not None:
            llm_cost = (llm_cost or 0.0) + float(llm["total_cost"])
        if llm.get("cost_reason"):
            cost_reasons.add(str(llm["cost_reason"]))

    stages_report: dict[str, Any] = {}
    overruns: list[dict[str, Any]] = []
    for name in sorted(set(TRACKED_STAGES) | set(stage_samples)):
        values = stage_samples.get(name) or []
        if not values:
            continue
        entry = {
            "sample_size": len(values),
            "p50_ms": _percentile(values, 50),
            "p95_ms": _percentile(values, 95),
            "max_ms": round(max(values), 2),
        }
        budget_ms = budgets.get("stage_p95_ms", {}).get(name)
        if budget_ms is not None:
            entry["budget_p95_ms"] = float(budget_ms)
            entry["within_budget"] = entry["p95_ms"] <= float(budget_ms)
            if not entry["within_budget"]:
                overruns.append(
                    {
                        "stage": name,
                        "p95_ms": entry["p95_ms"],
                        "budget_p95_ms": float(budget_ms),
                        "over_by_ms": round(entry["p95_ms"] - float(budget_ms), 2),
                    }
                )
        stages_report[name] = entry

    total_budget = budgets.get("end_to_end_p95_ms")
    end_to_end = {
        "sample_size": len(totals),
        "p50_ms": _percentile(totals, 50),
        "p95_ms": _percentile(totals, 95),
        "max_ms": round(max(totals), 2) if totals else 0.0,
    }
    if total_budget is not None:
        end_to_end["budget_p95_ms"] = float(total_budget)
        end_to_end["within_budget"] = end_to_end["p95_ms"] <= float(total_budget)
        if not end_to_end["within_budget"]:
            overruns.append(
                {
                    "stage": "end_to_end",
                    "p95_ms": end_to_end["p95_ms"],
                    "budget_p95_ms": float(total_budget),
                    "over_by_ms": round(end_to_end["p95_ms"] - float(total_budget), 2),
                }
            )

    # Cost is only reported when a provider actually returned one. The default
    # extractive provider makes no paid calls, so "unavailable" is the honest
    # answer -- not zero, which would read as a measured result.
    cost_report: dict[str, Any] = {
        "llm_calls": llm_calls,
        "total_tokens": llm_tokens,
        "total_cost_usd": llm_cost,
        "cost_status": "estimated" if llm_cost is not None else "unavailable",
        "cost_reasons": sorted(cost_reasons) or ["no_provider_pricing_table"],
    }
    cost_budget = budgets.get("cost_per_run_usd")
    if cost_budget is not None and llm_cost is not None:
        per_run = llm_cost / max(1, len(metrics))
        cost_report["cost_per_run_usd"] = round(per_run, 6)
        cost_report["budget_per_run_usd"] = float(cost_budget)
        cost_report["within_budget"] = per_run <= float(cost_budget)
        if not cost_report["within_budget"]:
            overruns.append(
                {
                    "stage": "cost_per_run",
                    "value": round(per_run, 6),
                    "budget": float(cost_budget),
                }
            )

    return {
        "runs_measured": len(metrics),
        "stages": stages_report,
        "end_to_end": end_to_end,
        "cost": cost_report,
        "overruns": overruns,
        "within_all_budgets": not overruns,
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def to_markdown(report: dict[str, Any]) -> list[str]:
    """Render the budget section of the scorecard."""
    lines = [
        "## Cost & latency budget",
        "",
        f"Runs measured: **{report['runs_measured']}** · "
        f"within all budgets: **{report['within_all_budgets']}**",
        "",
        "| Stage | n | p50 ms | p95 ms | max ms | budget p95 | ok |",
        "|---|---:|---:|---:|---:|---:|:--:|",
    ]
    for name, entry in report["stages"].items():
        budget = entry.get("budget_p95_ms")
        ok = entry.get("within_budget")
        lines.append(
            f"| `{name}` | {entry['sample_size']} | {entry['p50_ms']} | {entry['p95_ms']} "
            f"| {entry['max_ms']} | {budget if budget is not None else '—'} "
            f"| {'✅' if ok else ('❌' if ok is False else '—')} |"
        )
    e2e = report["end_to_end"]
    lines.append(
        f"| **end-to-end** | {e2e['sample_size']} | {e2e['p50_ms']} | {e2e['p95_ms']} "
        f"| {e2e['max_ms']} | {e2e.get('budget_p95_ms', '—')} "
        f"| {'✅' if e2e.get('within_budget') else ('❌' if 'within_budget' in e2e else '—')} |"
    )

    cost = report["cost"]
    lines += [
        "",
        f"LLM calls: **{cost['llm_calls']}** · tokens: **{cost['total_tokens']}** · "
        f"cost: **{cost['total_cost_usd'] if cost['total_cost_usd'] is not None else 'unavailable'}** "
        f"({', '.join(cost['cost_reasons'])})",
    ]
    if report["overruns"]:
        lines += ["", "**Budget overruns:**", ""]
        lines += [f"- `{o['stage']}` {o}" for o in report["overruns"]]
    return lines
