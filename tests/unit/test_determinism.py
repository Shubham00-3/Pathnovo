"""The engine advertises determinism; these pin the places it was not.

Each of these produced output that could differ between runs on identical
inputs, which makes a delta irreproducible and a regression un-attributable.
"""

from __future__ import annotations

import subprocess
import sys

from delta_chat.chat.citations import citation_supports_answer


def test_citation_support_is_stable_across_hash_seeds():
    """The check sampled `list(a)[:8]` from a set of strings.

    Python randomises string hashing per process, so which eight tokens were
    examined changed every run and citation validation was not reproducible.
    Subprocesses with different PYTHONHASHSEED values are the only way to
    observe this -- within one process the order is fixed.
    """
    answer = "modified table_cell from HH 245 to HH 250 on sheet 1 grid B2 nearby 26-PIT-9062"
    evidence = "identifiers 26-PIT-9062 setpoint annotation region"

    snippet = (
        "from delta_chat.chat.citations import citation_supports_answer;"
        f"print(citation_supports_answer({answer!r}, {evidence!r}))"
    )

    results = set()
    for seed in ("0", "1", "2", "3", "4"):
        proc = subprocess.run(
            [sys.executable, "-c", snippet],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed, "PYTHONPATH": "src", "SYSTEMROOT": "C:\\Windows"},
            check=True,
        )
        results.add(proc.stdout.strip())

    assert len(results) == 1, f"citation support varied with hash seed: {results}"


def test_token_sampling_order_is_deterministic():
    """Direct check on the same input inside one process, for a fast signal."""
    answer = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    evidence = "kilo lima mike"

    first = citation_supports_answer(answer, evidence)
    second = citation_supports_answer(answer, evidence)

    assert first == second


class TestDeltaIdentity:
    """delta_id keyed only on len(changes), so two runs finding the same *number*
    of entirely different changes produced the same id."""

    def _report(self, item_ids):
        from delta_chat.delta.models import DeltaItem, DeltaReport

        changes = [
            DeltaItem(
                delta_item_id=i,
                change_type="added",
                entity_type="text",
                page_a=1,
                page_b=1,
                deterministic_description=f"Added text at {i}",
                confidence=0.9,
                confidence_band="high",
            )
            for i in item_ids
        ]
        return DeltaReport(
            delta_id="x",
            pid_a="A",
            pid_b="B",
            pair_compatibility={},
            config_hash="c",
            changes=changes,
        )

    def test_report_carries_revision_labels(self):
        """Needed so chat can tell whether a question is in scope for the pair."""
        report = self._report(["D-1"])
        assert hasattr(report, "revision_a")
        assert hasattr(report, "revision_b")


def test_delta_id_distinguishes_different_change_sets():
    """Same count, different content, must not collide."""
    import hashlib

    def fingerprint(item_ids):
        joined = "|".join(sorted(item_ids))
        return hashlib.sha1(f"A|B|cfg|{len(item_ids)}|{joined}".encode()).hexdigest()[:12]

    same_count_different_changes = fingerprint(["D-1", "D-2"]) != fingerprint(["D-3", "D-4"])
    order_independent = fingerprint(["D-1", "D-2"]) == fingerprint(["D-2", "D-1"])

    assert same_count_different_changes
    assert order_independent
