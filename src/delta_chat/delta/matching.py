"""Element candidate scoring and Hungarian assignment."""

from __future__ import annotations

from typing import Any

import numpy as np
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from delta_chat.canonical.coordinates import (
    bbox_centroid,
    bbox_iou,
    centroid_distance,
    transform_bbox_affine,
)
from delta_chat.canonical.models import CanonicalElement
from delta_chat.errors import ResourceLimitError

COMPAT_KINDS = {
    "text": {"text", "note", "table_cell", "dimension"},
    "note": {"note", "text"},
    "equipment_tag": {"equipment_tag", "text"},
    "instrument_tag": {"instrument_tag", "text"},
    "line_tag": {"line_tag", "text"},
    "dimension": {"dimension", "text", "table_cell"},
    "table_cell": {"table_cell", "text", "dimension"},
    "symbol": {"symbol", "geometry_cluster"},
    "geometry_cluster": {"geometry_cluster", "symbol", "image_region"},
    "image_region": {"image_region", "geometry_cluster"},
    "markup_cloud": {"markup_cloud", "geometry_cluster"},
}


def _kind_compat(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if b in COMPAT_KINDS.get(a, set()) or a in COMPAT_KINDS.get(b, set()):
        return 0.7
    return 0.0


def score_pair(
    ea: CanonicalElement,
    eb: CanonicalElement,
    *,
    matrix: list[list[float]] | None,
    weights: dict[str, float],
) -> tuple[float, dict[str, float]]:
    bbox_a = ea.bbox
    if matrix is not None:
        bbox_a = list(transform_bbox_affine(ea.bbox, matrix))

    id_score = 0.0
    if ea.identifiers and eb.identifiers:
        sa, sb = set(x.upper() for x in ea.identifiers), set(x.upper() for x in eb.identifiers)
        if sa & sb:
            id_score = 1.0
        elif sa and sb:
            # partial
            id_score = max(
                (fuzz.ratio(x, y) / 100.0 for x in sa for y in sb),
                default=0.0,
            )

    ta, tb = ea.normalized_text or "", eb.normalized_text or ""
    if ta or tb:
        text_score = fuzz.token_set_ratio(ta, tb) / 100.0
        char_score = fuzz.ratio(ta, tb) / 100.0
        text_score = 0.6 * text_score + 0.4 * char_score
    else:
        text_score = 0.5 if ea.kind == "geometry_cluster" and eb.kind == "geometry_cluster" else 0.0

    iou = bbox_iou(bbox_a, eb.bbox)
    dist = centroid_distance(bbox_a, eb.bbox)
    spatial = 0.6 * iou + 0.4 * max(0.0, 1.0 - dist / 0.2)

    type_score = _kind_compat(ea.kind, eb.kind)
    # Neighbor weight redistributed: do not advertise a fake topology feature.
    # If neighbors are present on both elements, compute Jaccard; else use 0 and
    # reweight via zero contribution (documented in config comments).
    na = set(ea.neighbors or [])
    nb = set(eb.neighbors or [])
    if na or nb:
        neighbor = len(na & nb) / max(1, len(na | nb))
    else:
        neighbor = 0.0
    # geometry size
    wa = max(1e-6, bbox_a[2] - bbox_a[0]) * max(1e-6, bbox_a[3] - bbox_a[1])
    wb = max(1e-6, eb.bbox[2] - eb.bbox[0]) * max(1e-6, eb.bbox[3] - eb.bbox[1])
    geometry = 1.0 - min(1.0, abs(wa - wb) / max(wa, wb))

    feats = {
        "identifier": id_score,
        "text": text_score,
        "spatial": spatial,
        "type": type_score,
        "neighbor": neighbor,
        "geometry": geometry,
        "iou": iou,
        "centroid_distance": dist,
    }
    if type_score <= 0:
        return 0.0, feats

    w_id = weights.get("identifier", 0.3)
    w_text = weights.get("text", 0.22)
    w_spatial = weights.get("spatial", 0.18)
    w_type = weights.get("type", 0.1)
    w_neighbor = weights.get("neighbor", 0.0)  # default off unless neighbors populated
    w_geom = weights.get("geometry", 0.1)
    # If neighbor weight was left at legacy 0.1 with no neighbors, fold into spatial/text
    if w_neighbor > 0 and neighbor == 0.0 and not na and not nb:
        w_spatial += w_neighbor * 0.5
        w_text += w_neighbor * 0.5
        w_neighbor = 0.0

    score = (
        w_id * id_score
        + w_text * text_score
        + w_spatial * spatial
        + w_type * type_score
        + w_neighbor * neighbor
        + w_geom * geometry
    )
    # boost exact identifier anchors
    if id_score >= 1.0 and text_score > 0.6:
        score = min(1.0, score + 0.1)
    return float(score), feats


def match_elements(
    elems_a: list[CanonicalElement],
    elems_b: list[CanonicalElement],
    *,
    matrix: list[list[float]] | None,
    config: dict,
) -> dict[str, Any]:
    mcfg = config.get("matching", {})
    radius = float(mcfg.get("spatial_radius_norm", 0.12))
    min_score = float(mcfg.get("min_match_score", 0.42))
    weights = mcfg.get("weights", {})

    n, m = len(elems_a), len(elems_b)
    if n == 0 or m == 0:
        return {
            "matches": [],
            "unmatched_a": [e.element_id for e in elems_a],
            "unmatched_b": [e.element_id for e in elems_b],
            "scores": {},
        }

    max_pair_comparisons = int(mcfg.get("max_pair_comparisons", 2_000_000))
    pair_comparisons = n * m
    if pair_comparisons > max_pair_comparisons:
        raise ResourceLimitError(
            "Element matching would exceed the configured comparison limit",
            details={
                "elements_a": n,
                "elements_b": m,
                "pair_comparisons": pair_comparisons,
                "max_pair_comparisons": max_pair_comparisons,
            },
        )

    cost = np.ones((n, m), dtype=np.float64)
    feat_map: dict[tuple[int, int], dict[str, float]] = {}
    for i, ea in enumerate(elems_a):
        bbox_a = list(transform_bbox_affine(ea.bbox, matrix)) if matrix else ea.bbox
        ca = bbox_centroid(bbox_a)
        for j, eb in enumerate(elems_b):
            cb = bbox_centroid(eb.bbox)
            dist = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5
            # allow larger radius for strong identifier matches later
            if dist > radius * 2.5 and not (
                set(x.upper() for x in ea.identifiers) & set(x.upper() for x in eb.identifiers)
            ):
                continue
            s, feats = score_pair(ea, eb, matrix=matrix, weights=weights)
            if s <= 0:
                continue
            cost[i, j] = 1.0 - s
            feat_map[(i, j)] = feats

    ri, cj = linear_sum_assignment(cost)
    matches = []
    used_a, used_b = set(), set()
    for i, j in zip(ri, cj, strict=False):
        s = 1.0 - float(cost[i, j])
        if s < min_score:
            continue
        matches.append(
            {
                "element_a": elems_a[i].element_id,
                "element_b": elems_b[j].element_id,
                "index_a": i,
                "index_b": j,
                "score": round(s, 4),
                "features": feat_map.get((i, j), {}),
            }
        )
        used_a.add(i)
        used_b.add(j)

    return {
        "matches": matches,
        "unmatched_a": [elems_a[i].element_id for i in range(n) if i not in used_a],
        "unmatched_b": [elems_b[j].element_id for j in range(m) if j not in used_b],
    }
