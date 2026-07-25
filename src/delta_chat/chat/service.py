"""Grounded answer service with deterministic paths + LLM fallback."""

from __future__ import annotations

import re
from typing import Any

from delta_chat.chat.citations import validate_citations
from delta_chat.chat.llm import build_llm_client
from delta_chat.chat.models import ChatAnswer, Citation
from delta_chat.chat.prompts import SYSTEM, build_grounded_prompt
from delta_chat.delta.models import DeltaReport
from delta_chat.errors import CitationValidationError
from delta_chat.observability.llm_telemetry import LLMTelemetry
from delta_chat.observability.tracing import Tracer
from delta_chat.retrieval.hybrid import HybridRetriever, route_query


class ChatService:
    def __init__(
        self,
        retriever: HybridRetriever,
        delta: DeltaReport,
        config: dict,
        telemetry: LLMTelemetry | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.retriever = retriever
        self.delta = delta
        self.config = config
        self.telemetry = telemetry
        self.tracer = tracer
        self.llm = build_llm_client(config, telemetry=telemetry)

    def _deterministic(self, question: str) -> ChatAnswer | None:
        q = question.lower().strip()
        if "high-confidence" in q or "high confidence" in q:
            items = [c for c in self.delta.changes if c.confidence_band == "high"]
            if not items:
                text = "There are no high-confidence changes in the delta report."
                sid = f"D:{self.delta.delta_id}:summary"
                return ChatAnswer(
                    answer=text,
                    citations=[Citation(source_id=sid, source_family="delta", quote=text)],
                    confidence="high",
                    unsupported=False,
                    route="deterministic_high_conf",
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
                provider="deterministic",
            )

        if ("how many" in q and "added" in q) or q.startswith("how many") and "added" in q:
            n = sum(1 for c in self.delta.changes if c.change_type == "added")
            sid = f"D:{self.delta.delta_id}:summary"
            return ChatAnswer(
                answer=f"{n} items were classified as added.",
                citations=[
                    Citation(
                        source_id=sid,
                        source_family="delta",
                        quote=str(self.delta.summary),
                    )
                ],
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

    def _evidence_support_score(self, question: str, hits: list[dict]) -> float:
        """Score whether retrieved evidence can support the question (no phrase blacklist)."""
        if not hits:
            return 0.0
        stop = {
            "what",
            "when",
            "where",
            "which",
            "does",
            "did",
            "the",
            "any",
            "this",
            "that",
            "with",
            "from",
            "about",
            "near",
            "only",
            "show",
            "list",
            "how",
            "many",
            "were",
            "was",
            "are",
            "and",
            "for",
            "change",
            "changed",
            "please",
            "tell",
            "between",
            "revisions",
            "revision",
            "value",
            "table",
            "there",
        }
        q_tokens = {t for t in re.findall(r"[a-z0-9\-]{3,}", question.lower()) if t not in stop}
        top = float(hits[0].get("score") or 0.0)
        if not q_tokens:
            return top
        blob = " ".join((h.get("record") or {}).get("text", "") for h in hits[:8]).lower()
        # Prefer distinctive tokens (len>=4); require majority present in evidence
        content = [t for t in q_tokens if len(t) >= 4]
        if not content:
            content = list(q_tokens)
        present = [t for t in content if t in blob]
        missing = [t for t in content if t not in blob]
        overlap = len(present) / max(1, len(content))
        # If half or more distinctive tokens are absent, evidence cannot support the claim
        if missing and len(missing) >= max(1, (len(content) + 1) // 2):
            return min(0.04, 0.2 * top)
        return 0.45 * top + 0.55 * overlap

    def _refusal(self, hits: list[dict], route: str, reason: str) -> ChatAnswer:
        return ChatAnswer(
            answer=(
                f"I could not find enough evidence in {self.delta.pid_a}, "
                f"{self.delta.pid_b}, or their delta report to answer that."
            ),
            citations=[],
            confidence="low",
            unsupported=True,
            route=route,
            retrieval=hits,
            provider="refusal",
            meta={"refusal_reason": reason},
        )

    def _out_of_scope_revision(self, question: str) -> str | None:
        """Revision label named in the question that this pair does not contain.

        Asked "what is the duty value in revision C?" against an A/B pair, the
        retriever happily returns Rev B's duty and the system answers it: the
        question looks like every other duty question, and nothing downstream
        checks that revision C exists. That is a confidently wrong answer about
        a document that was never compared, so it is caught here rather than
        left to the support threshold, which has no notion of revision scope.
        """
        labels = {
            str(self.delta.revision_a or "").strip().upper(),
            str(self.delta.revision_b or "").strip().upper(),
        }
        labels.discard("")
        if not labels:
            return None

        # The \b after the keyword is load-bearing: without it, "between the
        # revisions" matches the plural "s" as a revision label and every
        # comparison question gets refused as out of scope.
        for match in re.finditer(r"\brev(?:ision)?\b\s*[:\-]?\s*([A-Z0-9]{1,3})\b", question, re.I):
            asked = match.group(1).strip().upper()
            # Bare digits are usually a revision *number* in prose ("revision 2
            # of the note"), and pages/sheets share that shape; only treat a
            # token as a revision label when it looks like one.
            if asked in labels:
                continue
            if asked.isdigit() and asked not in labels:
                return asked
            if asked.isalpha():
                return asked
        return None

    def ask(self, question: str) -> ChatAnswer:
        tracer = self.tracer

        def _run() -> ChatAnswer:
            out_of_scope = self._out_of_scope_revision(question)
            if out_of_scope is not None:
                ans = self._refusal([], "general", "revision_out_of_scope")
                ans.answer = (
                    f"This comparison covers revisions "
                    f"{self.delta.revision_a} and {self.delta.revision_b} only; "
                    f"revision {out_of_scope} is not part of it."
                )
                if tracer:
                    with tracer.span("answer.refusal", reason="revision_out_of_scope"):
                        pass
                return ans

            det = self._deterministic(question)
            if det is not None:
                if tracer:
                    with tracer.span("answer.deterministic", route=det.route or ""):
                        pass
                return det

            with self._span("retrieval.query"):
                hits = self.retriever.search(question)
                routing = route_query(question)
                top = float(hits[0]["score"]) if hits else 0.0
                support = self._evidence_support_score(question, hits)

            if tracer and tracer.spans:
                # annotate last retrieval span
                for sp in reversed(tracer.spans):
                    if sp.get("name") == "retrieval.query":
                        sp.setdefault("attributes", {})
                        sp["attributes"].update(
                            {
                                "hit_count": len(hits),
                                "top_score": top,
                                "support_score": round(support, 4),
                                "intent": routing.get("intent"),
                            }
                        )
                        break

            # Support threshold: refuse when retrieval cannot ground the claim
            if not hits or support < 0.18 or top < 0.01:
                ans = self._refusal(hits, routing.get("intent", "general"), "weak_retrieval")
                if tracer:
                    with tracer.span("answer.refusal", reason="weak_retrieval"):
                        pass
                return ans

            prompt = build_grounded_prompt(
                question,
                pid_a=self.delta.pid_a,
                pid_b=self.delta.pid_b,
                evidence=hits,
            )

            with self._span("prompt.build", prompt_chars=len(prompt)):
                pass

            try:
                with self._span(
                    "llm.answer",
                    provider=getattr(self.llm, "provider", "unknown"),
                ):
                    raw = self.llm.answer(prompt, system=SYSTEM)
            except Exception as exc:  # noqa: BLE001
                if tracer:
                    with tracer.span("llm.error", error=str(exc)):
                        pass
                return ChatAnswer(
                    answer=(
                        f"LLM failed: {exc}. I cannot produce a grounded answer without a valid model response."
                    ),
                    citations=[],
                    confidence="low",
                    unsupported=True,
                    route=routing.get("intent"),
                    retrieval=hits,
                    provider=getattr(self.llm, "provider", "unknown"),
                    meta={"error": str(exc)},
                )

            unsupported = bool(raw.get("unsupported"))
            raw_cites = list(raw.get("citations") or [])

            try:
                with self._span("citation.validate", attempt=1):
                    cites = validate_citations(
                        raw_cites,
                        hits,
                        require_for_factual=not unsupported,
                        unsupported=unsupported,
                        answer_text=str(raw.get("answer") or ""),
                    )
            except CitationValidationError as first_err:
                # One constrained retry: only allowed IDs, refuse if unsupported
                allowed = [h["source_id"] for h in hits[:8]]
                retry_prompt = (
                    prompt
                    + "\n\nCITATION VALIDATION FAILED: "
                    + str(first_err.message)
                    + "\nYou may ONLY cite these source_ids: "
                    + ", ".join(allowed)
                    + "\nIf evidence does not support the claim, set unsupported=true and citations=[]."
                    + "\nReturn strict JSON only."
                )
                try:
                    with self._span("llm.answer.retry", provider=getattr(self.llm, "provider", "")):
                        raw = self.llm.answer(retry_prompt, system=SYSTEM)
                    unsupported = bool(raw.get("unsupported"))
                    with self._span("citation.validate", attempt=2):
                        cites = validate_citations(
                            list(raw.get("citations") or []),
                            hits,
                            require_for_factual=not unsupported,
                            unsupported=unsupported,
                            answer_text=str(raw.get("answer") or ""),
                        )
                except Exception:  # noqa: BLE001
                    ans = self._refusal(hits, "citation_failure", "citation_retry_failed")
                    if tracer:
                        with tracer.span("answer.refusal", reason="citation_retry_failed"):
                            pass
                    return ans

            # Never substitute citations. If factual and empty after validation → refusal
            if not unsupported and not cites:
                ans = self._refusal(hits, "citation_failure", "no_valid_citations")
                if tracer:
                    with tracer.span("answer.refusal", reason="no_valid_citations"):
                        pass
                return ans

            answer = ChatAnswer(
                answer=str(raw.get("answer") or ""),
                citations=cites,
                confidence=str(raw.get("confidence") or "medium"),
                unsupported=unsupported,
                route=routing.get("intent"),
                retrieval=hits,
                provider=getattr(self.llm, "provider", "unknown"),
            )
            if tracer:
                with tracer.span(
                    "answer",
                    unsupported=unsupported,
                    citation_count=len(cites),
                    provider=answer.provider,
                ):
                    pass
            return answer

        return _run()

    def _span(self, name: str, **attrs: Any):
        if self.tracer is not None:
            return self.tracer.span(name, **attrs)
        from contextlib import nullcontext

        return nullcontext()
