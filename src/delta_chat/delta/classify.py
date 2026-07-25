"""Change classification and confidence."""

from __future__ import annotations

import re
from typing import Any

from delta_chat.canonical.coordinates import centroid_distance, transform_bbox_affine
from delta_chat.canonical.models import CanonicalElement


def _numeric_tokens(text: str) -> tuple[str, ...]:
    """Return normalized engineering-number tokens from extracted text."""
    return tuple(re.findall(r"(?<![A-Z])[-+]?\d+(?:[.,]\d+)?", (text or "").upper()))


def _is_ocr(element: CanonicalElement) -> bool:
    return (element.attributes or {}).get("source") == "ocr"


def _whitespace_only_difference(a: str, b: str) -> bool:
    """True when two strings differ only in whitespace placement."""
    squashed_a = re.sub(r"\s+", "", a or "")
    squashed_b = re.sub(r"\s+", "", b or "")
    return bool(squashed_a) and squashed_a == squashed_b


def classify_match(
    ea: CanonicalElement,
    eb: CanonicalElement,
    *,
    features: dict[str, float],
    matrix: list[list[float]] | None,
    move_tol: float,
) -> tuple[str, dict[str, Any]]:
    bbox_a = list(transform_bbox_affine(ea.bbox, matrix)) if matrix else list(ea.bbox)
    dist = centroid_distance(bbox_a, eb.bbox)
    text_same = (ea.normalized_text or "") == (eb.normalized_text or "")
    text_sim = float(features.get("text", 0.0))
    moved = dist > move_tol
    numeric_changed = _numeric_tokens(ea.normalized_text) != _numeric_tokens(eb.normalized_text)
    # Fuzzy similarity is useful for OCR noise, but it must never hide a changed
    # engineering value such as 12000 -> 12500 or HH 245 -> HH 250.
    content_changed = (not text_same) and (numeric_changed or text_sim < 0.92)
    # Where the text came from OCR, word segmentation is an artifact of the
    # recognizer, not of the drawing: "NOTE 10: See package" and
    # "NOTE10:Seepackage" are the same ink. Reporting that as a modification is
    # a false positive a reviewer has to triage. Only whitespace *placement* is
    # forgiven -- if any glyph or digit differs the change still stands, so
    # 12000 -> 12500 is unaffected. Native PDFs keep real spacing, so this is
    # deliberately restricted to OCR-sourced elements on both sides.
    if (
        content_changed
        and _is_ocr(ea)
        and _is_ocr(eb)
        and _whitespace_only_difference(ea.normalized_text, eb.normalized_text)
    ):
        content_changed = False
    # geometry-only elements: treat strong spatial as same content
    if not ea.normalized_text and not eb.normalized_text:
        content_changed = float(features.get("geometry", 1.0)) < 0.7

    if not moved and not content_changed:
        return "unchanged", {"centroid_distance": dist}
    if moved and content_changed:
        return "moved_modified", {"centroid_distance": dist}
    if moved:
        return "moved", {"centroid_distance": dist}
    return "modified", {"centroid_distance": dist}


def confidence_for_change(
    *,
    change_type: str,
    match_score: float | None,
    features: dict[str, float] | None,
    extraction_conf: float,
    registration_conf: float,
    pair_score: float,
    bands: dict[str, float],
) -> tuple[float, str, dict[str, float]]:
    features = features or {}
    factors = {
        "pair_compatibility": pair_score,
        "registration_quality": registration_conf,
        "extraction_confidence": extraction_conf,
        "match_score": match_score or (0.8 if change_type in {"added", "removed"} else 0.5),
        "identifier_strength": float(features.get("identifier", 0.0)),
        "text_geometry_agreement": 0.5
        * (float(features.get("text", 0.5)) + float(features.get("spatial", 0.5))),
    }
    conf = (
        0.15 * factors["pair_compatibility"]
        + 0.15 * factors["registration_quality"]
        + 0.20 * factors["extraction_confidence"]
        + 0.30 * factors["match_score"]
        + 0.10 * factors["identifier_strength"]
        + 0.10 * factors["text_geometry_agreement"]
    )
    if change_type in {"added", "removed"} and factors["identifier_strength"] >= 1.0:
        conf = min(1.0, conf + 0.1)
    conf = float(max(0.0, min(1.0, conf)))
    high = float(bands.get("high", 0.78))
    med = float(bands.get("medium", 0.55))
    if conf >= high:
        band = "high"
    elif conf >= med:
        band = "medium"
    else:
        band = "low"
    return conf, band, factors
