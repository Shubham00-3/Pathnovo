"""Registration must return an A->B transform from every code path.

The ECC fallback previously returned B->A. Nothing caught it: the matrix is
consumed as A->B by visual_diff, classify and matching, so the sign error simply
doubled misalignment, and it only engaged when ORB had already failed -- the
low-texture scans where errors are hardest to attribute. These tests assert the
direction directly against a known translation.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from delta_chat.delta.registration import _ink_alignment, register_pages

SHIFT_X, SHIFT_Y = 7.0, 4.0


def _textured(width: int = 420, height: int = 320) -> np.ndarray:
    rng = np.random.default_rng(7)
    img = np.full((height, width), 255, np.uint8)
    for _ in range(90):
        x = int(rng.integers(20, width - 40))
        y = int(rng.integers(20, height - 30))
        cv2.rectangle(img, (x, y), (x + 13, y + 8), 0, -1)
    return cv2.GaussianBlur(img, (5, 5), 0)


@pytest.fixture
def shifted_pair(tmp_path):
    """Rev B is Rev A translated by a known, exact amount."""
    a = _textured()
    b = cv2.warpAffine(
        a, np.float32([[1, 0, SHIFT_X], [0, 1, SHIFT_Y]]), (a.shape[1], a.shape[0]), borderValue=255
    )
    pa, pb = tmp_path / "a.png", tmp_path / "b.png"
    cv2.imwrite(str(pa), a)
    cv2.imwrite(str(pb), b)
    return pa, pb


def test_registration_maps_a_onto_b_not_the_reverse(shifted_pair):
    """The sign of the translation is the whole bug."""
    pa, pb = shifted_pair

    reg = register_pages(pa, pb, {"registration": {}})
    matrix = np.asarray(reg["pixel_matrix"], dtype=np.float64)

    # A positive A->B shift must come back positive. The inverted call returned
    # (-7, -4) here, which is what shipped.
    assert matrix[0, 2] == pytest.approx(SHIFT_X, abs=1.5)
    assert matrix[1, 2] == pytest.approx(SHIFT_Y, abs=1.5)


def test_ecc_fallback_alone_also_maps_a_onto_b(shifted_pair, monkeypatch):
    """Force the ECC branch: it is the path that was wrong."""
    pa, pb = shifted_pair

    class _NoFeatureOrb:
        """Stands in for ORB on a low-texture scan: detects nothing."""

        def detectAndCompute(self, image, mask):  # noqa: N802 - OpenCV API name
            return [], None

    monkeypatch.setattr("cv2.ORB_create", lambda *a, **k: _NoFeatureOrb())

    reg = register_pages(pa, pb, {"registration": {}})

    assert reg["method"].startswith("ecc")
    matrix = np.asarray(reg["pixel_matrix"], dtype=np.float64)
    assert matrix[0, 2] == pytest.approx(SHIFT_X, abs=1.5)
    assert matrix[1, 2] == pytest.approx(SHIFT_Y, abs=1.5)


def test_registration_is_reproducible(shifted_pair):
    """RANSAC samples OpenCV's global RNG; unseeded it varies run to run while
    the engine advertises determinism."""
    pa, pb = shifted_pair

    first = register_pages(pa, pb, {"registration": {}})["pixel_matrix"]
    second = register_pages(pa, pb, {"registration": {}})["pixel_matrix"]

    assert np.allclose(np.asarray(first), np.asarray(second))


def test_ink_alignment_rewards_correct_and_punishes_inverted_transforms():
    """ECC's correlation coefficient sits near 1.0 on mostly-white drawings
    regardless of alignment, so the fallback self-certified. Ink-only agreement
    has to actually separate the two cases."""
    a = _textured()
    b = cv2.warpAffine(
        a, np.float32([[1, 0, SHIFT_X], [0, 1, SHIFT_Y]]), (a.shape[1], a.shape[0]), borderValue=255
    )

    correct = _ink_alignment(a, b, np.float32([[1, 0, SHIFT_X], [0, 1, SHIFT_Y]]))
    inverted = _ink_alignment(a, b, np.float32([[1, 0, -SHIFT_X], [0, 1, -SHIFT_Y]]))

    assert correct > 0.8
    assert inverted < correct


def test_ink_alignment_claims_nothing_on_blank_pages():
    blank = np.full((100, 100), 255, np.uint8)

    assert _ink_alignment(blank, blank, np.float32([[1, 0, 0], [0, 1, 0]])) == 0.0
