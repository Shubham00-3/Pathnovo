"""Residual visual geometry must not re-report ink a semantic change explains.

Every change here was a real false positive on the scanned eval case. The delta
engine reported an element, and the pixel-residual pass then reported the same
ink again as an unexplained geometry region.
"""

from __future__ import annotations

from delta_chat.delta.visual_diff import _containment, _iou


def test_identical_boxes_are_fully_contained():
    box = [0.1, 0.1, 0.2, 0.2]

    assert _containment(box, box) == 1.0


def test_disjoint_boxes_do_not_overlap():
    assert _containment([0.0, 0.0, 0.1, 0.1], [0.5, 0.5, 0.6, 0.6]) == 0.0


def test_small_fragment_inside_a_large_change_is_detected():
    """The NOTE 12 case: two ink blobs inside one added text line.

    IoU is near the area ratio here -- far below any usable threshold -- which
    is exactly why these survived suppression and were reported as separate
    changes.
    """
    note_line = [0.330, 0.865, 0.443, 0.881]
    fragment = [0.396, 0.871, 0.417, 0.880]

    assert _containment(fragment, note_line) > 0.9
    assert _iou(fragment, note_line) < 0.35  # the metric that used to be used


def test_residual_region_enclosing_a_reported_tag_is_detected():
    """The moved-transmitter case: the residual blob is larger than the tag box.

    Containment is deliberately symmetric so this direction is caught too.
    """
    reported_tag = [0.705, 0.357, 0.742, 0.371]
    residual = [0.704, 0.332, 0.742, 0.386]

    assert _containment(residual, reported_tag) > 0.9


def test_partial_overlap_stays_below_the_threshold():
    """Genuinely distinct adjacent changes must still both be reported."""
    a = [0.10, 0.10, 0.20, 0.20]
    b = [0.18, 0.18, 0.30, 0.30]

    assert _containment(a, b) < 0.6


def test_touching_boxes_do_not_count_as_overlap():
    assert _containment([0.1, 0.1, 0.2, 0.2], [0.2, 0.1, 0.3, 0.2]) == 0.0


def test_zero_area_boxes_do_not_divide_by_zero():
    assert _containment([0.1, 0.1, 0.1, 0.1], [0.0, 0.0, 1.0, 1.0]) == 0.0
