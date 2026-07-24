"""Pair compatibility checks before delta."""

from __future__ import annotations

import re
from typing import Any

from delta_chat.canonical.models import DocumentRevision
from delta_chat.errors import PairMismatchError

TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9\-/]{2,}", re.I)


def _stable_tokens(doc: DocumentRevision) -> set[str]:
    toks: set[str] = set()
    for page in doc.pages:
        for el in page.elements:
            for ident in el.identifiers:
                toks.add(ident.upper())
            for m in TOKEN_RE.findall(el.normalized_text or ""):
                toks.add(m.upper())
    return toks


def _primary_equipment(doc: DocumentRevision) -> set[str]:
    out: set[str] = set()
    for page in doc.pages:
        for el in page.elements:
            if el.kind == "equipment_tag":
                out.update(i.upper() for i in el.identifiers)
            for i in el.identifiers:
                if re.search(r"KA|KB|P-\d|TK-", i, re.I):
                    out.add(i.upper())
    return out


def assess_compatibility(
    doc_a: DocumentRevision, doc_b: DocumentRevision, config: dict
) -> dict[str, Any]:
    thr = float(config.get("pair_compatibility", {}).get("threshold", 0.65))
    reasons: list[str] = []
    score = 1.0

    if doc_a.underlying_document_id != doc_b.underlying_document_id:
        score -= 0.45
        reasons.append("underlying_document_id differs")

    tokens_a, tokens_b = _stable_tokens(doc_a), _stable_tokens(doc_b)
    if tokens_a and tokens_b:
        overlap = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))
    else:
        overlap = 0.0
    if overlap < 0.25:
        score -= 0.25
        reasons.append("stable-token overlap is low")
    elif overlap < 0.5:
        score -= 0.1
        reasons.append("stable-token overlap is moderate")

    eq_a, eq_b = _primary_equipment(doc_a), _primary_equipment(doc_b)
    if eq_a and eq_b and eq_a.isdisjoint(eq_b):
        score -= 0.25
        reasons.append("primary equipment tag differs")

    # title-ish text
    title_a = (doc_a.pages[0].page_text[:400] if doc_a.pages else "").lower()
    title_b = (doc_b.pages[0].page_text[:400] if doc_b.pages else "").lower()
    if title_a and title_b:
        words_a = set(title_a.split())
        words_b = set(title_b.split())
        t_overlap = len(words_a & words_b) / max(1, len(words_a | words_b))
        if t_overlap < 0.15:
            score -= 0.1
            reasons.append("title/block text differs strongly")

    score = max(0.0, min(1.0, score))
    compatible = score >= thr
    if not reasons and not compatible:
        reasons.append("overall compatibility below threshold")

    return {
        "compatible": compatible,
        "score": round(score, 3),
        "threshold": thr,
        "reasons": reasons,
        "token_overlap": round(overlap, 3),
        "equipment_a": sorted(eq_a),
        "equipment_b": sorted(eq_b),
        "underlying_a": doc_a.underlying_document_id,
        "underlying_b": doc_b.underlying_document_id,
    }


def enforce_compatibility(result: dict[str, Any], mode: str) -> dict[str, Any]:
    mode = (mode or "warn").lower()
    out = dict(result)
    out["mode"] = mode
    if result["compatible"]:
        out["action"] = "continue"
        return out
    if mode == "strict":
        raise PairMismatchError(
            "Document pair is not a plausible revision pair",
            details=out,
        )
    if mode == "force":
        out["action"] = "forced_continue"
        out["warning"] = "Cross-document comparison forced by user"
        return out
    # warn
    out["action"] = "continue_with_warning"
    out["warning"] = "Cross-document comparison (pair mismatch warning)"
    return out
