"""Deterministic judges for chat eval (no LLM judge required)."""

from __future__ import annotations

from typing import Any

from delta_chat.chat.citations import citation_supports_answer


def judge_chat(answer: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    text = (answer.get("answer") or "").lower()
    unsupported = bool(answer.get("unsupported"))
    citations = answer.get("citations") or []
    cite_ids = [c.get("source_id") if isinstance(c, dict) else str(c) for c in citations]

    exp_unsup = bool(expected.get("unsupported"))
    refusal_ok = unsupported == exp_unsup if "unsupported" in expected else True

    facts = expected.get("must_include") or []
    fact_hits = [f for f in facts if str(f).lower() in text]
    fact_ok = (len(fact_hits) == len(facts)) if facts else True
    if exp_unsup:
        fact_ok = True

    need_cite = expected.get("require_citation", not exp_unsup)
    cite_present_ok = (len(cite_ids) > 0) if need_cite else True
    if exp_unsup:
        cite_present_ok = True

    acceptable_refs = expected.get("acceptable_source_prefixes") or []
    cite_precision = 1.0
    if cite_ids and acceptable_refs:
        good = sum(1 for c in cite_ids if any(str(c).startswith(p) for p in acceptable_refs))
        cite_precision = good / len(cite_ids)

    # Real citation validity: ID present in answer citations with quote that supports answer
    valid = 0
    for c in citations:
        if not isinstance(c, dict):
            continue
        sid = c.get("source_id")
        quote = c.get("quote") or ""
        if not sid:
            continue
        if exp_unsup:
            valid += 1
            continue
        if (
            citation_supports_answer(answer.get("answer") or "", quote)
            or not (answer.get("answer") or "").strip()
        ):
            valid += 1
    citation_validity = (
        (valid / len(citations)) if citations else (1.0 if exp_unsup or not need_cite else 0.0)
    )

    return {
        "fact_ok": fact_ok,
        "refusal_ok": refusal_ok,
        "citation_present_ok": cite_present_ok,
        "citation_precision": cite_precision,
        "citation_validity": citation_validity,
        "fact_hits": fact_hits,
    }
