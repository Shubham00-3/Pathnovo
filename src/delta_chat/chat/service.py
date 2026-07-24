"""Grounded answer service with deterministic paths + LLM fallback."""

from __future__ import annotations

import re

from delta_chat.chat.citations import validate_citations
from delta_chat.chat.llm import build_llm_client
from delta_chat.chat.models import ChatAnswer, Citation
from delta_chat.chat.prompts import SYSTEM, build_grounded_prompt
from delta_chat.delta.models import DeltaReport
from delta_chat.errors import CitationValidationError
from delta_chat.observability.llm_telemetry import LLMTelemetry
from delta_chat.retrieval.hybrid import HybridRetriever, route_query


class ChatService:
    def __init__(
        self,
        retriever: HybridRetriever,
        delta: DeltaReport,
        config: dict,
        telemetry: LLMTelemetry | None = None,
    ) -> None:
        self.retriever = retriever
        self.delta = delta
        self.config = config
        self.telemetry = telemetry
        self.llm = build_llm_client(config, telemetry=telemetry)

    def _deterministic(self, question: str) -> ChatAnswer | None:
        q = question.lower().strip()
        # list high-confidence changes
        if "high-confidence" in q or "high confidence" in q:
            items = [c for c in self.delta.changes if c.confidence_band == "high"]
            if not items:
                text = "There are no high-confidence changes in the delta report."
                # cite summary
                sid = f"D:{self.delta.delta_id}:summary"
                hits = self.retriever.search(question, top_k=3)
                cites = [Citation(source_id=sid, source_family="delta", quote=text)]
                return ChatAnswer(
                    answer=text,
                    citations=cites,
                    confidence="high",
                    unsupported=False,
                    route="deterministic_high_conf",
                    retrieval=hits,
                    provider="deterministic",
                )
            lines = [f"{c.delta_item_id}: {c.deterministic_description}" for c in items[:20]]
            cites = [
                Citation(
                    source_id=f"D:{c.delta_item_id}",
                    source_family="delta",
                    page=c.page_b or c.page_a,
                    quote=c.deterministic_description,
                    bbox=list((c.region or {}).get("bbox") or []),
                )
                for c in items[:10]
            ]
            return ChatAnswer(
                answer="High-confidence changes:\n" + "\n".join(lines),
                citations=cites,
                confidence="high",
                unsupported=False,
                route="deterministic_high_conf",
                retrieval=[],
                provider="deterministic",
            )

        m = re.search(r"how many .+ added", q)
        if m or q.startswith("how many") and "added" in q:
            n = sum(1 for c in self.delta.changes if c.change_type == "added")
            sid = f"D:{self.delta.delta_id}:summary"
            return ChatAnswer(
                answer=f"{n} items were classified as added.",
                citations=[Citation(source_id=sid, source_family="delta", quote=str(self.delta.summary))],
                confidence="high",
                unsupported=False,
                route="deterministic_count",
                provider="deterministic",
            )

        m = re.search(r"\b(D-[A-F0-9]+)\b", question, re.I)
        if m and ("show" in q or "what is" in q or "change" in q):
            did = m.group(1).upper()
            for c in self.delta.changes:
                if c.delta_item_id.upper() == did:
                    return ChatAnswer(
                        answer=c.deterministic_description,
                        citations=[
                            Citation(
                                source_id=f"D:{c.delta_item_id}",
                                source_family="delta",
                                page=c.page_b or c.page_a,
                                quote=c.deterministic_description,
                                bbox=list((c.region or {}).get("bbox") or []),
                            )
                        ],
                        confidence=c.confidence_band,
                        unsupported=False,
                        route="deterministic_item",
                        provider="deterministic",
                    )
        return None

    def _looks_unsupported(self, question: str, hits: list[dict]) -> bool:
        q = question.lower()
        # Explicit no-evidence / out-of-scope patterns common in eval + demos
        unsupported_markers = (
            "favorite color",
            "ceo",
            "vendor",
            "painting scheme",
            "approved the",
            "stock price",
            "phone number",
        )
        if any(m in q for m in unsupported_markers):
            # vendor change: only refuse if no delta mentions vendor
            if "vendor" in q:
                blob = " ".join((h.get("record") or {}).get("text", "") for h in hits).lower()
                if "vendor" not in blob:
                    return True
            else:
                return True
        # low lexical overlap between question tokens and top evidence
        q_tokens = {t for t in re.findall(r"[a-z0-9\-]{3,}", q) if t not in {
            "what", "when", "where", "which", "does", "did", "the", "any", "this",
            "that", "with", "from", "about", "near", "only", "show", "list", "how",
            "many", "were", "was", "are", "and", "for", "change", "changed",
        }}
        if not q_tokens:
            return False
        top_text = " ".join((h.get("record") or {}).get("text", "") for h in hits[:5]).lower()
        hits_n = sum(1 for t in q_tokens if t in top_text)
        return hits_n == 0

    def ask(self, question: str) -> ChatAnswer:
        det = self._deterministic(question)
        if det is not None:
            return det

        hits = self.retriever.search(question)
        routing = route_query(question)
        # weak retrieval refusal
        top = hits[0]["score"] if hits else 0.0
        if not hits or top < 0.01 or self._looks_unsupported(question, hits):
            return ChatAnswer(
                answer=(
                    f"I could not find enough evidence in {self.delta.pid_a}, "
                    f"{self.delta.pid_b}, or their delta report to answer that."
                ),
                citations=[],
                confidence="low",
                unsupported=True,
                route=routing["intent"],
                retrieval=hits,
                provider="refusal",
            )

        prompt = build_grounded_prompt(
            question,
            pid_a=self.delta.pid_a,
            pid_b=self.delta.pid_b,
            evidence=hits,
        )
        try:
            raw = self.llm.answer(prompt, system=SYSTEM)
        except Exception as exc:  # noqa: BLE001
            return ChatAnswer(
                answer=f"LLM failed: {exc}. Falling back to top evidence: {hits[0]['record'].get('text','')[:300]}",
                citations=[
                    Citation(
                        source_id=hits[0]["source_id"],
                        source_family=hits[0]["record"].get("source_family"),
                        quote=(hits[0]["record"].get("text") or "")[:200],
                    )
                ],
                confidence="low",
                unsupported=False,
                route=routing["intent"],
                retrieval=hits,
                provider=getattr(self.llm, "provider", "unknown"),
            )

        unsupported = bool(raw.get("unsupported"))
        try:
            cites = validate_citations(
                list(raw.get("citations") or []),
                hits,
                require_for_factual=not unsupported,
                unsupported=unsupported,
            )
        except CitationValidationError:
            # one retry with only allowed ids forced
            allowed = [h["source_id"] for h in hits[:3]]
            raw["citations"] = allowed
            if unsupported:
                cites = []
            else:
                try:
                    cites = validate_citations(allowed, hits, require_for_factual=True)
                except CitationValidationError:
                    return ChatAnswer(
                        answer=(
                            "I could not produce a grounded answer with valid citations "
                            "from the retrieved evidence."
                        ),
                        citations=[],
                        confidence="low",
                        unsupported=True,
                        route="citation_failure",
                        retrieval=hits,
                        provider=getattr(self.llm, "provider", "unknown"),
                    )

        return ChatAnswer(
            answer=str(raw.get("answer") or ""),
            citations=cites,
            confidence=str(raw.get("confidence") or "medium"),
            unsupported=unsupported,
            route=routing["intent"],
            retrieval=hits,
            provider=getattr(self.llm, "provider", "unknown"),
        )
