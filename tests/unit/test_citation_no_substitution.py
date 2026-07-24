import pytest

from delta_chat.chat.citations import validate_citations
from delta_chat.chat.service import ChatService
from delta_chat.delta.models import DeltaReport
from delta_chat.errors import CitationValidationError
from delta_chat.retrieval.hybrid import HybridRetriever
from delta_chat.retrieval.records import RetrievalRecord


def test_unknown_citation_rejected():
    evidence = [
        {"source_id": "D:D-1", "record": {"source_family": "delta", "text": "HH 250 changed"}}
    ]
    with pytest.raises(CitationValidationError):
        validate_citations(["D:FAKE"], evidence, answer_text="HH 250 changed")


def test_service_does_not_substitute_on_hallucination():
    class BadLLM:
        provider = "fake"

        def answer(self, prompt: str, *, system: str = ""):
            return {
                "answer": "The CEO favorite color is blue.",
                "citations": ["D:HALLUCINATED"],
                "confidence": "high",
                "unsupported": False,
            }

    records = [
        RetrievalRecord(
            source_id="D:D-1",
            source_family="delta",
            text="HH setpoint changed from 245 to 250",
            entity_type="table_cell",
        )
    ]
    delta = DeltaReport(
        delta_id="dd",
        pid_a="A",
        pid_b="B",
        pair_compatibility={"compatible": True, "score": 1},
        config_hash="x",
        summary={"total_changes": 1},
        changes=[],
    )
    svc = ChatService(HybridRetriever(records, {}), delta, {"llm": {"provider": "fake"}})
    svc.llm = BadLLM()
    ans = svc.ask("What is the CEO favorite color?")
    # Must refuse or fail grounded — never attach substituted valid IDs to hallucinated claim
    assert (
        ans.unsupported is True
        or not ans.citations
        or all(c.source_id != "D:D-1" or "CEO" not in ans.answer for c in ans.citations)
    )
    if not ans.unsupported and ans.citations:
        # if it answered, citations must have been in retrieved set AND support claim
        assert all(c.source_id in {"D:D-1"} for c in ans.citations)
        assert "CEO" not in ans.answer  # should not keep hallucinated claim with fake cites
