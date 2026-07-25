"""`/api/eval/latest` must degrade to the committed baseline, and say so.

Run artifacts live on the container filesystem and are ephemeral, so a freshly
deployed instance has none. Without a fallback the Evaluation tab reads "no
evaluation artifacts yet" even though the repo carries a full scorecard.
"""

from __future__ import annotations

import json

import delta_chat.api as api


def _isolate(tmp_path, monkeypatch, *, with_baseline: bool, with_run: bool):
    """Point the API at a scratch project root with a chosen artifact layout."""
    if with_baseline:
        (tmp_path / "eval").mkdir(parents=True, exist_ok=True)
        (tmp_path / "eval" / "baseline.json").write_text(
            json.dumps(
                {
                    "run_id": "baseline-run",
                    "native_delta_f1": 0.9231,
                    "all_gates_passed": True,
                    "gates": {"native_delta_f1": True},
                }
            ),
            encoding="utf-8",
        )
    if with_run:
        run_dir = tmp_path / "artifacts" / "eval" / "live-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "scorecard.json").write_text(
            json.dumps({"summary": {"run_id": "live-run", "native_delta_f1": 0.5}}),
            encoding="utf-8",
        )
        (run_dir / "scorecard.md").write_text("# live", encoding="utf-8")

    monkeypatch.setattr(api, "project_root", lambda: tmp_path)


def test_a_real_run_is_preferred_over_the_baseline(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, with_baseline=True, with_run=True)

    out = api.latest_eval()

    assert out["available"] is True
    assert out["source"] == "run"
    assert out["run_id"] == "live-run"
    assert out["scorecard_md"] == "# live"


def test_baseline_is_served_when_no_run_artifacts_exist(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, with_baseline=True, with_run=False)

    out = api.latest_eval()

    assert out["available"] is True
    assert out["source"] == "baseline"
    assert out["scorecard"]["summary"]["native_delta_f1"] == 0.9231


def test_baseline_is_labelled_so_it_cannot_pass_as_a_live_run(tmp_path, monkeypatch):
    """The distinction is the point: a baseline is a record, not evidence the
    eval ran here."""
    _isolate(tmp_path, monkeypatch, with_baseline=True, with_run=False)

    assert api.latest_eval()["source"] == "baseline"

    _isolate(tmp_path, monkeypatch, with_baseline=True, with_run=True)
    assert api.latest_eval()["source"] == "run"


def test_unavailable_when_neither_exists(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, with_baseline=False, with_run=False)

    out = api.latest_eval()

    assert out["available"] is False
    assert out["source"] is None


def test_a_corrupt_baseline_does_not_crash_the_endpoint(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, with_baseline=False, with_run=False)
    (tmp_path / "eval").mkdir(parents=True, exist_ok=True)
    (tmp_path / "eval" / "baseline.json").write_text("{not json", encoding="utf-8")

    assert api.latest_eval()["available"] is False


def test_a_run_directory_without_a_scorecard_is_skipped(tmp_path, monkeypatch):
    """An interrupted eval leaves an empty directory; it must not shadow the
    baseline and produce a KeyError."""
    _isolate(tmp_path, monkeypatch, with_baseline=True, with_run=False)
    (tmp_path / "artifacts" / "eval" / "empty-run").mkdir(parents=True, exist_ok=True)

    out = api.latest_eval()

    assert out["source"] == "baseline"
