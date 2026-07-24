"""Text grouping and identifier extraction for P&ID-like drawings."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from delta_chat.canonical.coordinates import quantize_bbox
from delta_chat.canonical.models import CanonicalElement, ElementKind, SourceRef

INSTRUMENT_RE = re.compile(
    r"\b\d{1,3}-?(?:PIT|PT|TT|TI|FT|FI|LT|LI|PSH|PSL|PAHH|PALL|TSH|TSL|FSH|FSL)[-\s]?\d{2,5}\b",
    re.I,
)
EQUIPMENT_RE = re.compile(r"\b\d{1,3}-?(?:KA|KB|P|V|E|C|TK|F)[-\s]?\d{2,5}\b", re.I)
LINE_TAG_RE = re.compile(r"\b\d{1,2}\"?\s*[A-Z]{1,4}-\d{2,5}-[A-Z0-9]+\b")
NOTE_RE = re.compile(r"\bNOTE\s+\d+\b", re.I)
SETPOINT_RE = re.compile(r"\b(?:HH|H|LL|L|SP)\s*[=:]?\s*\d+(?:\.\d+)?\b", re.I)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def extract_identifiers(text: str) -> list[str]:
    found: list[str] = []
    for rx in (INSTRUMENT_RE, EQUIPMENT_RE, LINE_TAG_RE, NOTE_RE):
        for m in rx.finditer(text or ""):
            found.append(normalize_text(m.group(0)).upper().replace(" ", ""))
    out: list[str] = []
    seen: set[str] = set()
    for x in found:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def classify_kind(text: str, identifiers: list[str] | None = None) -> ElementKind:
    t = text or ""
    ids = identifiers or extract_identifiers(t)
    if NOTE_RE.search(t):
        return "note"
    if any(INSTRUMENT_RE.search(i) for i in ids):
        return "instrument_tag"
    if any(EQUIPMENT_RE.search(i) for i in ids):
        return "equipment_tag"
    if LINE_TAG_RE.search(t):
        return "line_tag"
    if re.search(r"\b(duty|setpoint|hh|ll|capacity|kw|bar|psi)\b", t, re.I) or SETPOINT_RE.search(
        t
    ):
        return "table_cell"
    return "text"


def stable_element_id(
    *,
    pid: str,
    page_number: int,
    kind: str,
    normalized_text: str,
    bbox: Iterable[float],
) -> str:
    qb = quantize_bbox(list(bbox), q=0.01)
    payload = f"{pid}|{page_number}|{kind}|{normalized_text}|{qb}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"e_{digest}"


def make_element(
    *,
    pid: str,
    page_number: int,
    raw_text: str,
    bbox: list[float],
    kind: ElementKind | None = None,
    confidence: float = 1.0,
    attributes: dict | None = None,
    sheet_id: str | None = None,
    grid_region: str | None = None,
) -> CanonicalElement:
    norm = normalize_text(raw_text)
    ids = extract_identifiers(norm)
    k = kind or classify_kind(norm, ids)
    eid = stable_element_id(
        pid=pid, page_number=page_number, kind=k, normalized_text=norm, bbox=bbox
    )
    return CanonicalElement(
        element_id=eid,
        kind=k,
        raw_text=raw_text,
        normalized_text=norm,
        bbox=list(bbox),
        identifiers=ids,
        attributes=attributes or {},
        extraction_confidence=confidence,
        source_ref=SourceRef(
            pid=pid,
            page_number=page_number,
            sheet_id=sheet_id,
            grid_region=grid_region,
            bbox=list(bbox),
            element_ids=[eid],
            quote=norm[:200] if norm else None,
        ),
    )


def estimate_grid(
    bbox: list[float],
    *,
    cols: int = 8,
    rows: int = 6,
    convention: str = "row_letter_col_number",
    approximate: bool = True,
) -> str:
    """Map normalized centroid to an engineering-style grid label.

    Default convention for this project (synthetic samples):
    - columns numbered 1..N left-to-right
    - rows lettered A.. top-to-bottom
    Label format: ``A3`` (row letter + column number).

    Supplied AutoCAD sheets often use letters on one axis and numbers on the
    other; when title-block detection is unavailable, results are marked
    approximate via the trailing ``~`` when ``approximate`` is True.
    """
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    col = min(cols - 1, max(0, int(cx * cols)))
    row = min(rows - 1, max(0, int(cy * rows)))
    if convention == "col_letter_row_number":
        # legacy / alternate: letter from x, number from y
        label = f"{chr(ord('A') + col)}{row + 1}"
    else:
        # preferred: row letter + column number
        label = f"{chr(ord('A') + row)}{col + 1}"
    return f"{label}~" if approximate else label
