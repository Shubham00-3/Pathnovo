"""Change classification and confidence."""

from __future__ import annotations

from typing import Any

from delta_chat.canonical.coordinates import centroid_distance, transform_bbox_affine
from delta_chat.canonical.models import CanonicalElement


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
    content_changed = (not text_same) and text_sim < 0.92
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
