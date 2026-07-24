"""Citation validation against retrieved evidence."""

from __future__ import annotations

from typing import Any

from delta_chat.chat.models import Citation
from delta_chat.errors import CitationValidationError


def validate_citations(
    raw_citations: list[str],
    evidence: list[dict[str, Any]],
    *,
    require_for_factual: bool = True,
    unsupported: bool = False,
) -> list[Citation]:
    allowed = {}
    for e in evidence:
        sid = e.get("source_id")
        rec = e.get("record") or {}
        if sid:
            allowed[sid] = rec

    validated: list[Citation] = []
    unknown: list[str] = []
    for c in raw_citations or []:
        sid = str(c).strip()
        if sid not in allowed:
            unknown.append(sid)
            continue
        rec = allowed[sid]
        validated.append(
            Citation(
                source_id=sid,
                source_family=rec.get("source_family"),
                pid=rec.get("pid"),
                page=rec.get("page"),
                grid_region=rec.get("grid_region"),
                quote=(rec.get("text") or "")[:240],
                bbox=list(rec.get("bbox") or []),
            )
        )

    if unknown:
        raise CitationValidationError(
            f"Unknown or non-retrieved citations: {unknown}",
            details={"unknown": unknown, "allowed": sorted(allowed.keys())},
        )
    if require_for_factual and not unsupported and not validated:
        raise CitationValidationError(
            "Factual answer requires at least one validated citation",
            details={"allowed": sorted(allowed.keys())},
        )
    return validated
