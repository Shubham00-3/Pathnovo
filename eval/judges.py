"""Deterministic judges for chat eval (no LLM judge required).

Two properties matter more than the scores these produce:

1. A judge must be able to fail. Anything that grades the production code's own
   output with the production code's own filter measures nothing.
2. A match must be a real match. Naive substring containment passes "250"
   against "12500", which turns a wrong answer into a green tick.
"""

from __future__ import annotations

import re
from typing import Any


def fact_present(fact: str, text: str) -> bool:
    """Whether `fact` appears in `text` as a standalone value.

    Plain `in` is wrong for engineering values: the expected setpoint "250" is a
    substring of the unrelated duty "12500", and "185" of "1185", so a wrong
    answer scores as correct. Numeric facts are matched on digit boundaries;
    text facts fall back to whitespace-insensitive containment because OCR
    output varies in spacing.
    """
    fact = str(fact).strip().lower()
    if not fact:
        return True
    text = (text or "").lower()

    if re.fullmatch(r"[\d.,]+", fact):
        # Not \b: that treats "." and "," as boundaries, so "12.5" would match
        # inside "112.5". Guard with explicit lookaround instead.
        #
        # The trailing guard must not reject ordinary sentence punctuation. A
        # naive (?![\d.,]) refuses "...changed to 250." for the full stop, and
        # "12.5, ok" for the comma. Separators only mean the number continues
        # when a digit follows them -- "12,500" and "112.5" continue, "250." and
        # "12.5," do not.
        pattern = rf"(?<![\d.,]){re.escape(fact)}(?!\d|[.,]\d)"
        return re.search(pattern, text) is not None

    squash = re.compile(r"\s+")
    return squash.sub("", fact) in squash.sub("", text)


def judge_chat(answer: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    text = (answer.get("answer") or "").lower()
    unsupported = bool(answer.get("unsupported"))
    citations = answer.get("citations") or []
    cite_ids = [c.get("source_id") if isinstance(c, dict) else str(c) for c in citations]

    exp_unsup = bool(expected.get("unsupported"))
    refusal_ok = unsupported == exp_unsup if "unsupported" in expected else True

    facts = expected.get("must_include") or []
    fact_hits = [f for f in facts if fact_present(f, text)]
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

    # Citation groundedness, measured against the labels rather than against the
    # production filter.
    #
    # The previous metric called citation_supports_answer(answer, quote) -- the
    # same function chat/citations.py already uses to DISCARD failing citations
    # before returning. Every citation the judge saw had, by construction,
    # passed that exact test, so the score could not be anything but 1.00. It
    # graded a filter on its own output.
    #
    # This asks a question the system can fail: does the evidence it cited
    # actually contain the value the answer was supposed to report? A confident
    # answer citing a source that does not contain the number is precisely the
    # failure worth catching, and it now scores zero.
    quotes = [str(c.get("quote") or "") for c in citations if isinstance(c, dict)]
    if exp_unsup or not need_cite:
        # A refusal cites nothing; there is no groundedness claim to check.
        citation_groundedness = 1.0
    elif not quotes:
        citation_groundedness = 0.0
    elif not facts:
        # No labelled value to trace. Fall back to the weaker structural claim
        # that a citation exists, and mark it so the scorecard is not read as
        # stronger evidence than it is.
        citation_groundedness = 1.0
    else:
        grounded = sum(1 for f in facts if any(fact_present(f, q) for q in quotes))
        citation_groundedness = grounded / len(facts)

    return {
        "fact_ok": fact_ok,
        "refusal_ok": refusal_ok,
        "citation_present_ok": cite_present_ok,
        "citation_precision": cite_precision,
        "citation_groundedness": citation_groundedness,
        # True when groundedness was assertable from labels rather than defaulted.
        "groundedness_measured": bool(facts and not exp_unsup and need_cite),
        "fact_hits": fact_hits,
    }
