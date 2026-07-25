from __future__ import annotations

from scripts.make_secondary_pid_pair import main


def test_secondary_fixture_is_independent_and_clean(tmp_path) -> None:
    payload = main(out_dir=tmp_path)
    assert payload["pid_a"] == "PID-SYN2-A"
    assert payload["pid_b"] == "PID-SYN2-B"
    assert payload["underlying_document_id"] == "DOC-SYN-EXPORT"
    assert len(payload["controlled_changes"]) == 4
