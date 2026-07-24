"""Coordinate helpers: top-left origin, normalized [0,1] boxes."""

from __future__ import annotations

from collections.abc import Sequence

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def normalize_bbox(
    bbox: Sequence[float],
    *,
    page_width: float,
    page_height: float,
    origin: str = "top-left",
) -> BBox:
    """Normalize a page-space bbox to [0,1] top-left coordinates."""
    x0, y0, x1, y1 = map(float, bbox[:4])
    if origin == "bottom-left":
        # PDF user space is often bottom-left; convert to top-left first.
        y0_tl = page_height - y1
        y1_tl = page_height - y0
        y0, y1 = y0_tl, y1_tl
    if page_width <= 0 or page_height <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    nx0 = clamp01(min(x0, x1) / page_width)
    ny0 = clamp01(min(y0, y1) / page_height)
    nx1 = clamp01(max(x0, x1) / page_width)
    ny1 = clamp01(max(y0, y1) / page_height)
    return (nx0, ny0, nx1, ny1)


def denormalize_bbox(
    bbox: Sequence[float],
    *,
    page_width: float,
    page_height: float,
) -> BBox:
    x0, y0, x1, y1 = map(float, bbox[:4])
    return (
        x0 * page_width,
        y0 * page_height,
        x1 * page_width,
        y1 * page_height,
    )


def bbox_iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax0, ay0, ax1, ay1 = map(float, a[:4])
    bx0, by0, bx1, by1 = map(float, b[:4])
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def bbox_centroid(b: Sequence[float]) -> tuple[float, float]:
    x0, y0, x1, y1 = map(float, b[:4])
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def centroid_distance(a: Sequence[float], b: Sequence[float]) -> float:
    ax, ay = bbox_centroid(a)
    bx, by = bbox_centroid(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def quantize_bbox(b: Sequence[float], q: float = 0.01) -> tuple[int, int, int, int]:
    return tuple(int(round(float(v) / q)) for v in b[:4])  # type: ignore[return-value]


def transform_bbox_affine(bbox: Sequence[float], matrix: list[list[float]]) -> BBox:
    """Apply 2x3 affine matrix to bbox corners; return axis-aligned hull."""
    x0, y0, x1, y1 = map(float, bbox[:4])
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    a, b, tx = matrix[0]
    c, d, ty = matrix[1]
    xs, ys = [], []
    for x, y in corners:
        xs.append(a * x + b * y + tx)
        ys.append(c * x + d * y + ty)
    return (min(xs), min(ys), max(xs), max(ys))
