"""Residual visual geometry detector after registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from delta_chat.canonical.grouping import estimate_grid, make_element
from delta_chat.canonical.models import CanonicalElement


def residual_geometry_changes(
    render_a: str | Path,
    render_b: str | Path,
    *,
    pixel_matrix: list[list[float]],
    config: dict,
    pid_b: str,
    page_number: int,
    existing_boxes: list[list[float]],
) -> list[dict[str, Any]]:
    vcfg = config.get("visual_diff", {})
    if not vcfg.get("enabled", True):
        return []
    min_area = int(vcfg.get("min_component_area", 80))
    max_comp = int(vcfg.get("max_components", 40))
    thr = int(vcfg.get("residual_threshold", 28))
    border = float(vcfg.get("suppress_border_ratio", 0.03))

    ga = cv2.imread(str(render_a), cv2.IMREAD_GRAYSCALE)
    gb = cv2.imread(str(render_b), cv2.IMREAD_GRAYSCALE)
    if ga is None or gb is None:
        return []
    if ga.shape != gb.shape:
        ga = cv2.resize(ga, (gb.shape[1], gb.shape[0]))
    M = np.array(pixel_matrix, dtype=np.float32)
    warped = cv2.warpAffine(ga, M, (gb.shape[1], gb.shape[0]), flags=cv2.INTER_LINEAR)
    # normalize contrast
    warped = cv2.normalize(warped, None, 0, 255, cv2.NORM_MINMAX)
    gb_n = cv2.normalize(gb, None, 0, 255, cv2.NORM_MINMAX)
    diff = cv2.absdiff(warped, gb_n)
    _, bw = cv2.threshold(diff, thr, 255, cv2.THRESH_BINARY)
    # suppress anti-alias noise
    kernel = np.ones((3, 3), np.uint8)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel, iterations=1)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel, iterations=2)

    h, w = bw.shape[:2]
    bx, by = int(w * border), int(h * border)
    bw[:by, :] = 0
    bw[-by:, :] = 0
    bw[:, :bx] = 0
    bw[:, -bx:] = 0

    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bw, connectivity=8)
    candidates: list[dict[str, Any]] = []
    for i in range(1, n_labels):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        ww = int(stats[i, cv2.CC_STAT_WIDTH])
        hh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if ww * hh > 0.25 * w * h:
            continue
        nb = [x / w, y / h, (x + ww) / w, (y + hh) / h]
        # dedupe against existing semantic boxes
        if any(_iou(nb, eb) > 0.35 for eb in existing_boxes):
            continue
        candidates.append(
            {
                "bbox": nb,
                "area": area,
                "grid_region": estimate_grid(nb),
                "page": page_number,
            }
        )
        if len(candidates) >= max_comp:
            break

    # merge nearby components coarsely
    merged: list[dict[str, Any]] = []
    for c in sorted(candidates, key=lambda x: -x["area"]):
        if any(_iou(c["bbox"], m["bbox"]) > 0.15 for m in merged):
            continue
        merged.append(c)
    max_emit = int(vcfg.get("max_emit", 2))
    return merged[:max_emit]


def _iou(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    bb = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    return inter / max(1e-9, aa + bb - inter)


def geometry_delta_items(
    components: list[dict[str, Any]],
    *,
    pid_a: str,
    pid_b: str,
) -> list[CanonicalElement]:
    """Helper to create synthetic geometry elements if needed."""
    out = []
    for i, c in enumerate(components):
        out.append(
            make_element(
                pid=pid_b,
                page_number=int(c["page"]),
                raw_text=f"geometry_region_{i}",
                bbox=list(c["bbox"]),
                kind="geometry_cluster",
                confidence=0.6,
                grid_region=c.get("grid_region"),
            )
        )
    return out
