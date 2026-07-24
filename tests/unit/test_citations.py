import pytest

from delta_chat.chat.citations import validate_citations
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
