from delta_chat.canonical.coordinates import (
    bbox_iou,
    denormalize_bbox,
    normalize_bbox,
    quantize_bbox,
)


def test_normalize_roundtrip():
    raw = (100.0, 50.0, 200.0, 150.0)
    n = normalize_bbox(raw, page_width=1000, page_height=500, origin="top-left")
    d = denormalize_bbox(n, page_width=1000, page_height=500)
    assert abs(d[0] - 100) < 1e-6
    assert abs(d[3] - 150) < 1e-6


def test_iou_and_quantize():
    a = (0.0, 0.0, 0.5, 0.5)
    b = (0.25, 0.25, 0.75, 0.75)
    assert 0.1 < bbox_iou(a, b) < 0.3
    q = quantize_bbox(a, q=0.01)
    assert q == (0, 0, 50, 50)
