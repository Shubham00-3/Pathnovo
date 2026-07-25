"""Citation validation against retrieved evidence."""

from __future__ import annotations

import re
from typing import Any

from delta_chat.chat.models import Citation
from delta_chat.errors import CitationValidationError


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9\-]{3,}", (text or "").lower())}


def citation_supports_answer(answer_text: str, evidence_text: str) -> bool:
    """Lightweight support check: some content overlap between answer and evidence."""
    a = _token_set(answer_text)
    e = _token_set(evidence_text)
    if not a or not e:
        # empty answer or evidence: do not claim support for factual answers
        return bool(e) and not a
    answer_numbers = set(re.findall(r"(?<![a-z])\d+(?:[.,]\d+)?", answer_text.lower()))
    evidence_numbers = set(re.findall(r"(?<![a-z])\d+(?:[.,]\d+)?", evidence_text.lower()))
    # A citation cannot support a numeric engineering claim if none of the
    # claimed values appear in that evidence. One matching value is sufficient
    # for an item-level citation in a multi-item summary.
    if answer_numbers and not (answer_numbers & evidence_numbers):
        return False
    overlap = len(a & e) / max(1, len(a))
    return overlap >= 0.12 or any(
        tok in evidence_text.lower() for tok in list(a)[:8] if len(tok) > 4
    )


def validate_citations(
    raw_citations: list[str],
    evidence: list[dict[str, Any]],
    *,
    require_for_factual: bool = True,
    unsupported: bool = False,
    answer_text: str = "",
) -> list[Citation]:
    allowed: dict[str, dict[str, Any]] = {}
    for e in evidence:
        sid = e.get("source_id")
        rec = e.get("record") or {}
        if sid:
            allowed[sid] = rec

    validated: list[Citation] = []
    unknown: list[str] = []
    unsupported_claims: list[str] = []

    for c in raw_citations or []:
        sid = str(c).strip()
        if sid not in allowed:
            unknown.append(sid)
            continue
        rec = allowed[sid]
        quote = (rec.get("text") or "")[:240]
        if answer_text and not unsupported and not citation_supports_answer(answer_text, quote):
            # valid ID but evidence does not support claim
            unsupported_claims.append(sid)
            continue
        validated.append(
            Citation(
                source_id=sid,
                source_family=rec.get("source_family"),
                pid=rec.get("pid"),
                page=rec.get("page"),
                grid_region=rec.get("grid_region"),
                quote=quote,
                bbox=list(rec.get("bbox") or []),
            )
        )

    if unknown:
        raise CitationValidationError(
            f"Unknown or non-retrieved citations: {unknown}",
            details={"unknown": unknown, "allowed": sorted(allowed.keys())},
        )
    if unsupported_claims and not validated:
        raise CitationValidationError(
            f"Citations do not support the answer claim: {unsupported_claims}",
            details={"unsupported_claims": unsupported_claims},
        )
    if require_for_factual and not unsupported and not validated:
        raise CitationValidationError(
            "Factual answer requires at least one validated supporting citation",
            details={"allowed": sorted(allowed.keys())},
        )
    return validated
