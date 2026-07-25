"""The regression comparator is the thing that proves a change helped or hurt.

If it cannot detect a regression it is decoration, so these tests assert the
detection itself, in both directions, including the silent-failure modes.
"""

from __future__ import annotations

from eval.compare import compare


def _card(**overrides):
    base = {
        "run_id": "run",
        "git_sha": "abc123",
        "native_delta_f1": 0.92,
        "scanned_delta_f1": 0.67,
        "cad_delta_f1": 0.83,
        "pair_mismatch_accuracy": 1.0,
        "citation_validity": 1.0,
        "unsupported_refusal_accuracy": 1.0,
        "chat_fact_accuracy": 1.0,
        "retrieval_recall_at_5": 1.0,
        "citation_precision": 1.0,
        "gates": {"native_delta_f1": True, "citation_validity": True},
    }
    base.update(overrides)
    return base


def test_identical_scorecards_report_no_regression():
    result = compare(_card(), _card())

    assert not result["has_regression"]
    assert all(r["status"] in {"unchanged", "absent"} for r in result["rows"])


def test_quality_drop_beyond_tolerance_is_a_regression():
    result = compare(_card(), _card(native_delta_f1=0.55))

    assert result["has_regression"]
    assert any(r["metric"] == "native_delta_f1" for r in result["regressions"])


def test_drop_within_tolerance_is_not_flagged():
    """Run-to-run noise must not cry wolf, or the signal gets ignored."""
    result = compare(_card(), _card(native_delta_f1=0.91))

    assert not result["has_regression"]


def test_improvement_is_reported_separately():
    result = compare(_card(), _card(scanned_delta_f1=0.85))

    assert not result["has_regression"]
    assert any(r["metric"] == "scanned_delta_f1" for r in result["improvements"])


def test_zero_tolerance_metrics_regress_on_any_drop():
    result = compare(_card(), _card(citation_validity=0.99))

    assert result["has_regression"]


def test_a_metric_that_disappears_counts_as_a_regression():
    """A case that stopped running would otherwise look like a clean scorecard."""
    result = compare(_card(), _card(cad_delta_f1=None))

    assert result["has_regression"]
    assert any(r["metric"] == "cad_delta_f1" and r["status"] == "missing" for r in result["rows"])


def test_a_newly_added_metric_is_not_a_regression():
    baseline = _card()
    del baseline["cad_delta_f1"]

    result = compare(baseline, _card())

    assert not result["has_regression"]
    assert any(r["metric"] == "cad_delta_f1" and r["status"] == "new" for r in result["rows"])


def test_a_gate_flipping_to_failing_is_a_regression():
    result = compare(_card(), _card(gates={"native_delta_f1": True, "citation_validity": False}))

    assert result["has_regression"]
    assert any(r["metric"] == "gate:citation_validity" for r in result["regressions"])


def test_large_latency_increase_is_a_regression():
    baseline = _card(budget={"end_to_end": {"p95_ms": 1000.0}})
    current = _card(budget={"end_to_end": {"p95_ms": 5000.0}})

    result = compare(baseline, current)

    assert result["has_regression"]
    assert result["latency"]["status"] == "regressed"


def test_modest_latency_variation_is_tolerated():
    baseline = _card(budget={"end_to_end": {"p95_ms": 1000.0}})
    current = _card(budget={"end_to_end": {"p95_ms": 1200.0}})

    result = compare(baseline, current)

    assert not result["has_regression"]
