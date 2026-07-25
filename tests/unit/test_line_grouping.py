"""Word grouping must be revision-stable or the delta engine invents changes."""

from __future__ import annotations

from delta_chat.ingest.ocr.base import OcrWord
from delta_chat.ingest.scanned_pdf import _group_into_lines


def _w(text: str, x0: float, y0: float, x1: float, y1: float) -> OcrWord:
    return OcrWord(text=text, confidence=0.95, bbox_px=(x0, y0, x1, y1))


def test_words_on_one_line_group_together():
    words = [_w("NOTE", 10, 100, 60, 118), _w("10:", 65, 101, 90, 119)]

    lines = _group_into_lines(words, page_width_px=1000)

    assert len(lines) == 1
    assert [w.text for w in lines[0]] == ["NOTE", "10:"]


def test_separate_columns_do_not_merge_across_a_wide_gap():
    """A duty table and a note at the same height are different elements."""
    words = [_w("Duty:", 10, 100, 60, 118), _w("NOTE", 800, 100, 860, 118)]

    lines = _group_into_lines(words, page_width_px=1000)

    assert len(lines) == 2


def test_grouping_is_stable_under_small_vertical_jitter():
    """Scans of one drawing differ by sub-line noise; grouping must not.

    This is the property that matters: fixed-height bucketing put these two
    words in different buckets on one revision and the same bucket on the other,
    which surfaced as a phantom added/removed pair in the delta.
    """
    rev_a = [_w("Motor:", 10, 100, 70, 118), _w("250kW", 75, 100, 140, 118)]
    rev_b = [_w("Motor:", 10, 107, 70, 125), _w("250kW", 75, 106, 140, 124)]

    lines_a = _group_into_lines(rev_a, page_width_px=1000)
    lines_b = _group_into_lines(rev_b, page_width_px=1000)

    assert len(lines_a) == len(lines_b) == 1
    assert [w.text for w in lines_a[0]] == [w.text for w in lines_b[0]]


def test_distinct_rows_stay_distinct():
    words = [_w("HH 245", 10, 100, 80, 118), _w("LL 100", 10, 140, 80, 158)]

    lines = _group_into_lines(words, page_width_px=1000)

    assert len(lines) == 2


def test_empty_input_is_handled():
    assert _group_into_lines([], page_width_px=1000) == []
