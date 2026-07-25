"""Residual visual geometry detector after registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from delta_chat.canonical.grouping import estimate_grid
from delta_chat.errors import ResourceLimitError


def _load_gray_bounded(path: str | Path, *, max_pixels: int) -> np.ndarray | None:
    try:
        with Image.open(path) as header:
            width, height = header.size
    except Exception:  # noqa: BLE001
        return None
    pixels = int(width) * int(height)
    if pixels > max_pixels:
        raise ResourceLimitError(
            "Rendered page exceeds the visual-diff pixel limit",
            details={"pixels": pixels, "max_image_pixels": max_pixels},
        )
    return cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)


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
    min_area = int(vcfg.get("min_component_area", 400))
    max_comp = int(vcfg.get("max_components", 20))
    thr = int(vcfg.get("residual_threshold", 40))
    border = float(vcfg.get("suppress_border_ratio", 0.05))
    # Policy cap: emit up to max_emit high-area components; remainder counted suppressed
    max_emit = int(vcfg.get("max_emit", 6))
    overlap_thr = float(vcfg.get("explained_overlap_threshold", 0.6))
    max_pixels = int(vcfg.get("max_image_pixels", config.get("max_render_pixels", 20_000_000)))

    ga = _load_gray_bounded(render_a, max_pixels=max_pixels)
    gb = _load_gray_bounded(render_b, max_pixels=max_pixels)
    if ga is None or gb is None:
        return []
    if ga.shape != gb.shape:
        ga = cv2.resize(ga, (gb.shape[1], gb.shape[0]))
    M = np.array(pixel_matrix, dtype=np.float32)
    warped = cv2.warpAffine(ga, M, (gb.shape[1], gb.shape[0]), flags=cv2.INTER_LINEAR)

    # Drawings are black ink on white paper. Added black ink means A is brighter
    # than B; removed black ink means B is brighter than registered A.
    diff_add = cv2.subtract(warped, gb)
    diff_rem = cv2.subtract(gb, warped)

    def components(diff: np.ndarray, change_type: str) -> list[dict[str, Any]]:
        _, bw = cv2.threshold(diff, thr, 255, cv2.THRESH_BINARY)
        kernel = np.ones((3, 3), np.uint8)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel, iterations=1)
        bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel, iterations=2)
        h, w = bw.shape[:2]
        bx, by = int(w * border), int(h * border)
        bw[:by, :] = 0
        bw[-by:, :] = 0
        bw[:, :bx] = 0
        bw[:, -bx:] = 0
        n_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(bw, connectivity=8)
        out: list[dict[str, Any]] = []
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
            # Suppress residual that a semantic change already accounts for.
            #
            # IoU is the wrong test here. A residual fragment sitting wholly
            # inside a larger reported change -- say two ink blobs inside an
            # added NOTE line -- has an IoU near the area ratio, far below any
            # sane threshold, so it survived and was reported as a second,
            # unexplained change. Containment is what actually matters: if the
            # smaller box is mostly inside the larger, it is the same ink.
            if any(_containment(nb, eb) > overlap_thr for eb in existing_boxes):
                continue
            out.append(
                {
                    "bbox": nb,
                    "area": area,
                    "grid_region": estimate_grid(nb, approximate=True),
                    "page": page_number,
                    "change_type": change_type,
                    "direction": change_type,
                }
            )
        return out

    candidates = components(diff_add, "added") + components(diff_rem, "removed")
    candidates.sort(key=lambda x: -x["area"])
    # merge nearby same-type
    merged: list[dict[str, Any]] = []
    for c in candidates:
        if any(
            m["change_type"] == c["change_type"] and _iou(c["bbox"], m["bbox"]) > 0.15
            for m in merged
        ):
            continue
        merged.append(c)
        if len(merged) >= max_comp:
            break

    emitted = merged[:max_emit]
    # attach suppressed count on first item for engine accounting
    if emitted:
        emitted[0]["suppressed_peers"] = max(0, len(merged) - max_emit)
    return emitted


def _intersection(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    return iw * ih


def _iou(a: list[float], b: list[float]) -> float:
    inter = _intersection(a, b)
    if inter <= 0:
        return 0.0
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    bb = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return inter / max(1e-9, aa + bb - inter)


def _containment(a: list[float], b: list[float]) -> float:
    """Fraction of the *smaller* box covered by the overlap.

    Symmetric on purpose: a residual blob inside a reported text box and a
    residual region enclosing a reported tag are both the same ink seen twice,
    and neither is caught by IoU.
    """
    inter = _intersection(a, b)
    if inter <= 0:
        return 0.0
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    bb = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return inter / max(1e-9, min(aa, bb))
