"""Cost/latency budget analysis over run metrics."""

from __future__ import annotations

import json

from eval.budget import analyze, collect_run_metrics


def _write_run(tmp_path, name: str, stages: dict, llm: dict | None = None):
    run_dir = tmp_path / name
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage_latency_ms": stages,
        "llm": llm or {"calls": 0, "total_tokens": 0, "total_cost": None},
    }
    (run_dir / "metrics.json").write_text(json.dumps(payload), encoding="utf-8")
    return run_dir


def test_missing_and_corrupt_metrics_are_skipped_not_fatal(tmp_path):
    good = _write_run(tmp_path, "good", {"delta.engine": 100.0})
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "metrics.json").write_text("{not json", encoding="utf-8")

    assert len(collect_run_metrics([good, bad, tmp_path / "absent"])) == 1


def test_percentiles_and_budget_pass(tmp_path):
    runs = [
        _write_run(tmp_path, f"r{i}", {"delta.engine": float(v)})
        for i, v in enumerate([100, 200, 300])
    ]

    report = analyze(runs, {"budgets": {"stage_p95_ms": {"delta.engine": 500}}})

    stage = report["stages"]["delta.engine"]
    assert stage["sample_size"] == 3
    assert stage["max_ms"] == 300.0
    assert stage["within_budget"] is True
    assert report["within_all_budgets"] is True


def test_overrun_is_reported_as_a_finding(tmp_path):
    runs = [_write_run(tmp_path, "r0", {"markup.overlay": 9000.0})]

    report = analyze(runs, {"budgets": {"stage_p95_ms": {"markup.overlay": 2000}}})

    assert report["within_all_budgets"] is False
    assert report["overruns"][0]["stage"] == "markup.overlay"
    assert report["overruns"][0]["over_by_ms"] == 7000.0


def test_end_to_end_sums_stages(tmp_path):
    runs = [_write_run(tmp_path, "r0", {"ingest.a": 100.0, "delta.engine": 200.0})]

    report = analyze(runs, {})

    assert report["end_to_end"]["p50_ms"] == 300.0


def test_cost_is_unavailable_rather_than_zero_without_a_paid_provider(tmp_path):
    """Reporting 0.00 would read as a measured cost; it is simply unknown."""
    runs = [
        _write_run(
            tmp_path,
            "r0",
            {"delta.engine": 10.0},
            llm={
                "calls": 3,
                "total_tokens": 500,
                "total_cost": None,
                "cost_reason": "no_provider_pricing_table",
            },
        )
    ]

    report = analyze(runs, {})

    assert report["cost"]["total_cost_usd"] is None
    assert report["cost"]["cost_status"] == "unavailable"
    assert report["cost"]["llm_calls"] == 3
    assert report["cost"]["total_tokens"] == 500


def test_cost_budget_is_evaluated_when_a_real_cost_exists(tmp_path):
    runs = [
        _write_run(
            tmp_path,
            "r0",
            {"delta.engine": 10.0},
            llm={"calls": 1, "total_tokens": 100, "total_cost": 0.20},
        )
    ]

    report = analyze(runs, {"budgets": {"cost_per_run_usd": 0.05}})

    assert report["cost"]["within_budget"] is False


def test_no_runs_produces_an_empty_but_valid_report():
    report = analyze([], {})

    assert report["runs_measured"] == 0
    assert report["within_all_budgets"] is True
