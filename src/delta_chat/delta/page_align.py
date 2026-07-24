"""One-to-one page alignment between revisions."""

from __future__ import annotations

from rapidfuzz import fuzz

from delta_chat.canonical.models import CanonicalPage, DocumentRevision


def page_similarity(a: CanonicalPage, b: CanonicalPage) -> float:
    score = 0.0
    if a.sheet_id and b.sheet_id and a.sheet_id == b.sheet_id:
        score += 0.35
    if a.page_number == b.page_number:
        score += 0.15
    # dimension similarity
    if a.width and b.width and a.height and b.height:
        ar_a = a.width / max(a.height, 1e-6)
        ar_b = b.width / max(b.height, 1e-6)
        score += 0.15 * max(0.0, 1.0 - abs(ar_a - ar_b))
    ta = (a.page_text or "")[:800]
    tb = (b.page_text or "")[:800]
    if ta and tb:
        score += 0.35 * (fuzz.token_set_ratio(ta, tb) / 100.0)
    return min(1.0, score)


def align_pages(doc_a: DocumentRevision, doc_b: DocumentRevision) -> list[dict]:
    """Return matched page pairs and unmatched pages."""
    remaining_b = set(range(len(doc_b.pages)))
    matches = []
    used_b: set[int] = set()
    for ia, pa in enumerate(doc_a.pages):
        best_j, best_s = None, -1.0
        for ib in remaining_b:
            s = page_similarity(pa, doc_b.pages[ib])
            if s > best_s:
                best_s, best_j = s, ib
        if best_j is not None and best_s >= 0.25:
            matches.append(
                {
                    "page_a": pa.page_number,
                    "page_b": doc_b.pages[best_j].page_number,
                    "index_a": ia,
                    "index_b": best_j,
                    "score": round(best_s, 3),
                }
            )
            used_b.add(best_j)
            remaining_b.discard(best_j)
    unmatched_a = [
        doc_a.pages[i].page_number
        for i in range(len(doc_a.pages))
        if i not in {m["index_a"] for m in matches}
    ]
    unmatched_b = [doc_b.pages[j].page_number for j in range(len(doc_b.pages)) if j not in used_b]
    return {
        "matches": matches,
        "unmatched_a": unmatched_a,
        "unmatched_b": unmatched_b,
    }  # type: ignore[return-value]
