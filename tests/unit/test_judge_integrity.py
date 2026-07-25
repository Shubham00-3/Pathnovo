"""The judges must be able to fail.

A metric that cannot go below 1.00 measures nothing. Two specific defects are
pinned here: substring matching that passed wrong numeric answers, and a
citation check that re-graded the production filter's own output.
"""

from __future__ import annotations

from eval.judges import fact_present, judge_chat


class TestFactMatching:
    def test_a_setpoint_does_not_match_inside_a_larger_number(self):
        """The bug: expected "250" scored a pass against duty "12500"."""
        assert fact_present("250", "duty: 12500 nm3/h") is False
        assert fact_present("185", "pressure 1185 kpa") is False

    def test_the_real_value_still_matches(self):
        assert fact_present("250", "modified from 'HH 245' to 'HH 250'") is True
        assert fact_present("12500", "duty: 12500 nm3/h") is True

    def test_decimals_are_not_matched_inside_longer_decimals(self):
        assert fact_present("12.5", "value 112.5 bar") is False
        assert fact_present("12.5", "value 12.5 bar") is True

    def test_thousands_separators_are_treated_as_continuation(self):
        assert fact_present("12", "total 12,500 units") is False

    def test_sentence_punctuation_is_not_treated_as_continuation(self):
        """Tightening the boundary check overshot at first and rejected a
        correct answer ending in a full stop, and one followed by a comma."""
        assert fact_present("250", "the setpoint changed to 250.") is True
        assert fact_present("12.5", "value 12.5, confirmed") is True
        assert fact_present("250", "(250)") is True

    def test_text_facts_tolerate_ocr_spacing(self):
        """OCR splits and joins words unpredictably; that is not a wrong answer."""
        assert fact_present("NOTE 12", "added note: NOTE12:Added for startup") is True
        assert fact_present("NOTE 12", "added note: NOTE 12: added for startup") is True

    def test_an_absent_fact_fails(self):
        assert fact_present("NOTE 99", "added note: NOTE 12") is False

    def test_empty_expectation_is_vacuously_true(self):
        assert fact_present("", "anything") is True


class TestCitationGroundedness:
    """The old metric called the same helper production used to discard failing
    citations, so every citation it saw had already passed. It could not fail."""

    def _answer(self, text, quote):
        return {
            "answer": text,
            "unsupported": False,
            "citations": [{"source_id": "D:1", "quote": quote}],
        }

    def test_citation_containing_the_expected_value_is_grounded(self):
        result = judge_chat(
            self._answer("The HH setpoint changed to 250.", "Modified 'HH 245' to 'HH 250'"),
            {"must_include": ["250"], "require_citation": True},
        )

        assert result["citation_groundedness"] == 1.0
        assert result["groundedness_measured"] is True

    def test_a_citation_missing_the_claimed_value_scores_zero(self):
        """This is the failure the old metric structurally could not report: a
        confident answer citing evidence that does not contain its own number."""
        result = judge_chat(
            self._answer("The HH setpoint changed to 250.", "Duty: 12000 Nm3/h unchanged"),
            {"must_include": ["250"], "require_citation": True},
        )

        assert result["citation_groundedness"] == 0.0

    def test_an_answer_with_no_citation_at_all_scores_zero(self):
        result = judge_chat(
            {"answer": "The setpoint is 250.", "unsupported": False, "citations": []},
            {"must_include": ["250"], "require_citation": True},
        )

        assert result["citation_groundedness"] == 0.0

    def test_refusals_are_not_penalised_for_citing_nothing(self):
        result = judge_chat(
            {"answer": "I could not find evidence.", "unsupported": True, "citations": []},
            {"unsupported": True, "must_include": [], "require_citation": False},
        )

        assert result["citation_groundedness"] == 1.0
        # ...but the scorecard must know this was not actually asserted.
        assert result["groundedness_measured"] is False

    def test_unlabelled_questions_are_flagged_as_unmeasured(self):
        """must_include: [] means there is nothing to trace a citation to."""
        result = judge_chat(
            self._answer("Some summary.", "Some evidence"),
            {"must_include": [], "require_citation": True},
        )

        assert result["groundedness_measured"] is False


class TestRefusalScoring:
    def test_answering_when_a_refusal_was_expected_fails(self):
        result = judge_chat(
            {"answer": "Duty: 12500 Nm3/h", "unsupported": False, "citations": []},
            {"unsupported": True, "must_include": [], "require_citation": False},
        )

        assert result["refusal_ok"] is False

    def test_refusing_when_a_refusal_was_expected_passes(self):
        result = judge_chat(
            {"answer": "Not enough evidence.", "unsupported": True, "citations": []},
            {"unsupported": True, "must_include": [], "require_citation": False},
        )

        assert result["refusal_ok"] is True
