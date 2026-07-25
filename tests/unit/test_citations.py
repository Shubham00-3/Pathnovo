import pytest

from delta_chat.chat.citations import citation_supports_answer, validate_citations
from delta_chat.errors import CitationValidationError


def test_rejects_unknown_citation():
    evidence = [{"source_id": "D:D-1", "record": {"source_family": "delta", "text": "x"}}]
    with pytest.raises(CitationValidationError):
        validate_citations(["D:FAKE"], evidence)


def test_accepts_known():
    evidence = [
        {"source_id": "D:D-1", "record": {"source_family": "delta", "text": "x", "pid": "P"}}
    ]
    cites = validate_citations(["D:D-1"], evidence)
    assert cites[0].source_id == "D:D-1"


def test_numeric_claim_requires_matching_value_in_evidence():
    assert not citation_supports_answer(
        "The HH setpoint changed to 9999.",
        "Modified HH setpoint from 245 to 250.",
    )
