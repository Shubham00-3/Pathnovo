"""Deterministic judges for chat eval (no LLM judge required)."""

from __future__ import annotations

from typing import Any


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

    # citation validity already enforced by system; here check non-empty when required
    need_cite = expected.get("require_citation", not exp_unsup)
    cite_ok = (len(cite_ids) > 0) if need_cite else True
    if exp_unsup:
        cite_ok = True

    acceptable_refs = expected.get("acceptable_source_prefixes") or []
    cite_precision = 1.0
    if cite_ids and acceptable_refs:
        good = sum(1 for c in cite_ids if any(c.startswith(p) for p in acceptable_refs))
        cite_precision = good / len(cite_ids)

    return {
        "fact_ok": fact_ok,
        "refusal_ok": refusal_ok,
        "citation_present_ok": cite_ok,
        "citation_precision": cite_precision,
        "citation_validity": 1.0,  # invalid citations rejected upstream
        "fact_hits": fact_hits,
    }
