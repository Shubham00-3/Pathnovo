from pathlib import Path

import pytest

from delta_chat.config import project_root
from delta_chat.pipeline import chat_on_run, run_pair


@pytest.fixture(scope="module")
def samples_ready():
    root = project_root()
    a = root / "data/samples/synthetic_native/lift_rev_a.pdf"
    if not a.exists():
        from scripts.build_eval_dataset import main as build_reg
        from scripts.make_scanned_pair import main as make_scan
        from scripts.make_synthetic_pid_pair import main as make_syn

        make_syn(seed=42)
        make_scan(seed=42)
        build_reg()
    yield


def test_native_pair_end_to_end(samples_ready):
    result = run_pair("PID-SYN-A", "PID-SYN-B", mismatch_mode="warn", request_id="test-native")
    run_dir = Path(result["run_dir"])
    for name in [
        "canonical_a.json",
        "canonical_b.json",
        "delta.json",
        "report.md",
        "report.html",
        "markup.pdf",
        "events.jsonl",
        "trace.json",
        "metrics.json",
    ]:
        assert (run_dir / name).exists(), name

    # should detect some changes
    assert result["delta"]["summary"]["total_changes"] >= 1
    assert result["delta"]["pair_compatibility"]["compatible"] is True

    ans = chat_on_run(result, "Summarize only high-confidence changes.")
    assert ans["unsupported"] is False
    assert ans["citations"]

    bad = chat_on_run(result, "What is the CEO favorite color on this drawing?")
    assert bad["unsupported"] is True


def test_mismatch_pair(samples_ready):
    root = project_root()
    # ensure registry
    from scripts.build_eval_dataset import main as build_reg

    build_reg()
    if not (root / "data/registry.json").exists():
        pytest.skip("no registry")
    result = run_pair("PID-LIFT", "PID-EXPORT", mismatch_mode="warn", request_id="test-mismatch")
    assert result["delta"]["pair_compatibility"]["compatible"] is False
    assert result["delta"]["warnings"]
