"""Image registration: ORB+RANSAC with ECC fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from delta_chat.errors import RegistrationFailure


def _load_gray(path: str | Path, *, max_pixels: int) -> np.ndarray:
    try:
        with Image.open(path) as header:
            width, height = header.size
    except Exception as exc:  # noqa: BLE001
        raise RegistrationFailure("Cannot inspect rendered page image") from exc
    pixels = int(width) * int(height)
    if pixels > max_pixels:
        raise RegistrationFailure(
            "Rendered page exceeds the registration pixel limit",
            details={"pixels": pixels, "max_image_pixels": max_pixels},
        )
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RegistrationFailure("Cannot read rendered page image")
    return img


def _ink_alignment(ga: np.ndarray, gb: np.ndarray, matrix: np.ndarray) -> float:
    """Agreement between B's ink and warped A's ink, in [0, 1].

    Engineering drawings are overwhelmingly white, so any whole-image similarity
    measure is dominated by matching blank paper and reports a high score for a
    badly aligned pair. Restricting the comparison to dark pixels makes the
    score responsive to the thing that actually matters.
    """
    warped = cv2.warpAffine(
        ga,
        matrix[:2].astype(np.float32),
        (gb.shape[1], gb.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    ink_a = warped < 128
    ink_b = gb < 128
    union = int(np.count_nonzero(ink_a | ink_b))
    if union == 0:
        # No ink on either page: nothing to align, so claim nothing.
        return 0.0
    return float(np.count_nonzero(ink_a & ink_b) / union)


def register_pages(
    render_a: str | Path,
    render_b: str | Path,
    config: dict,
) -> dict[str, Any]:
    cfg = config.get("registration", {})
    max_features = int(cfg.get("max_features", 2000))
    min_inliers = int(cfg.get("min_inliers", 12))
    max_err = float(cfg.get("max_reproj_error", 8.0))
    min_conf = float(cfg.get("min_confidence", 0.35))
    border = float(cfg.get("border_mask_ratio", 0.04))
    max_pixels = int(cfg.get("max_image_pixels", config.get("max_render_pixels", 20_000_000)))

    # RANSAC below samples from OpenCV's global RNG. Without a fixed seed the
    # same inputs can yield a different matrix run to run, which contradicts the
    # determinism this engine claims and makes a delta irreproducible.
    cv2.setRNGSeed(int(cfg.get("rng_seed", 42)))

    ga = _load_gray(render_a, max_pixels=max_pixels)
    gb = _load_gray(render_b, max_pixels=max_pixels)
    # resize A to B shape for consistent space
    if ga.shape != gb.shape:
        ga = cv2.resize(ga, (gb.shape[1], gb.shape[0]), interpolation=cv2.INTER_AREA)

    h, w = gb.shape[:2]
    mask = np.ones_like(ga, dtype=np.uint8) * 255
    bx, by = int(w * border), int(h * border)
    mask[:by, :] = 0
    mask[-by:, :] = 0
    mask[:, :bx] = 0
    mask[:, -bx:] = 0

    orb_create = getattr(cv2, "ORB_create")
    orb = orb_create(nfeatures=max_features)
    kpa, desa = orb.detectAndCompute(ga, mask)
    kpb, desb = orb.detectAndCompute(gb, mask)

    method = "none"
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
    inliers = 0
    residual = 0.0
    confidence = 0.0

    if desa is not None and desb is not None and len(kpa) >= 8 and len(kpb) >= 8:
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        knn = bf.knnMatch(desa, desb, k=2)
        good = []
        for pair in knn:
            if len(pair) != 2:
                continue
            m, n = pair
            if m.distance < 0.75 * n.distance:
                good.append(m)
        if len(good) >= 8:
            src = np.asarray(
                [[float(v) for v in kpa[m.queryIdx].pt] for m in good],
                dtype=np.float32,
            ).reshape(-1, 1, 2)
            dst = np.asarray(
                [[float(v) for v in kpb[m.trainIdx].pt] for m in good],
                dtype=np.float32,
            ).reshape(-1, 1, 2)
            M, inlier_mask = cv2.estimateAffinePartial2D(
                src, dst, method=cv2.RANSAC, ransacReprojThreshold=max_err
            )
            if M is not None and inlier_mask is not None:
                inliers = int(inlier_mask.sum())
                if inliers >= min_inliers:
                    matrix = M.astype(np.float64)
                    method = "orb_ransac"
                    # residual on inliers
                    pts = src[inlier_mask.ravel() == 1]
                    projected = cv2.transform(pts, M)
                    target = dst[inlier_mask.ravel() == 1]
                    residual = float(np.mean(np.linalg.norm(projected - target, axis=2)))
                    confidence = min(
                        1.0,
                        0.35
                        + 0.5 * (inliers / max(30, len(good)))
                        + 0.15 * max(0, 1 - residual / 10),
                    )

    if method == "none" or confidence < min_conf:
        # ECC fallback.
        #
        # Argument order is load-bearing and was previously inverted. OpenCV
        # returns the warp aligning `templateImage` onto `inputImage`, so
        # findTransformECC(ga, gb) yields A->B -- the same direction the ORB
        # branch produces via estimateAffinePartial2D(src=A_pts, dst=B_pts) and
        # the direction every consumer assumes. Passing (gb, ga) returns the
        # exact inverse, which roughly doubles misalignment instead of removing
        # it, and does so precisely on the low-texture scans where ORB failed
        # and this fallback is the only thing running. Verified against a known
        # translation in tests/unit/test_registration_direction.py.
        try:
            ga_f = ga.astype(np.float32) / 255.0
            gb_f = gb.astype(np.float32) / 255.0
            warp = np.eye(2, 3, dtype=np.float32)
            criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-5)
            cc, ecc_warp = cv2.findTransformECC(ga_f, gb_f, warp, cv2.MOTION_AFFINE, criteria)
            warp = np.asarray(ecc_warp, dtype=np.float32)
            matrix = warp.astype(np.float64)
            method = "ecc"
            # ECC's correlation coefficient is not a usable confidence here: on a
            # drawing that is ~95% white paper it sits near 1.0 whatever the
            # alignment does, so the fallback would always self-certify. Score
            # the alignment on ink only, where disagreement actually shows.
            ink_agreement = _ink_alignment(ga, gb, matrix)
            confidence = float(max(confidence, ink_agreement))
            residual = float(max(0.0, (1.0 - ink_agreement) * 10))
            inliers = max(inliers, 10)
        except Exception:
            pass

    # Validate affine transform plausibility
    a, b, tx = matrix[0]
    c, d, ty = matrix[1]
    det = float(a * d - b * c)
    scale = float(((a**2 + c**2) ** 0.5 + (b**2 + d**2) ** 0.5) / 2.0)
    implausible = (
        not np.isfinite(det)
        or abs(det) < 0.2
        or abs(det) > 5.0
        or scale < 0.5
        or scale > 2.0
        or residual > max_err * 3
    )
    if implausible and method != "none":
        confidence = min(confidence, 0.2)
        method = f"{method}_implausible"

    # Convert pixel affine to normalized-space affine for [0,1] boxes.
    norm_matrix = [
        [float(a), float(b * h / max(w, 1)), float(tx / max(w, 1))],
        [float(c * w / max(h, 1)), float(d), float(ty / max(h, 1))],
    ]

    rejected = (
        confidence < min_conf
        or method in {"none", "none_implausible"}
        or (method.endswith("_implausible") and confidence < min_conf)
    )
    result = {
        "method": method,
        "inliers": inliers,
        "residual_error": round(residual, 4),
        "confidence": round(float(confidence), 4),
        "pixel_matrix": matrix.tolist(),
        "norm_matrix": norm_matrix,
        "image_size": [int(w), int(h)],
        "determinant": round(det, 5),
        "scale": round(scale, 5),
        "rejected": rejected,
    }
    if rejected:
        result["warning"] = (
            "Registration quality below threshold or transform implausible; "
            "visual geometry diff suppressed"
        )
        # Do not trust identity for unrelated pages — mark explicit rejection
        if method == "none":
            result["norm_matrix"] = None
            result["pixel_matrix"] = None
    return result
