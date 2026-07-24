"""Image registration: ORB+RANSAC with ECC fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from delta_chat.errors import RegistrationFailure


def _load_gray(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RegistrationFailure(f"Cannot read render: {path}")
    return img


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

    ga = _load_gray(render_a)
    gb = _load_gray(render_b)
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

    orb = cv2.ORB_create(nfeatures=max_features)
    kpa, desa = orb.detectAndCompute(ga, mask)
    kpb, desb = orb.detectAndCompute(gb, mask)

    method = "identity"
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
    inliers = 0
    residual = 0.0
    confidence = 0.5

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
            src = np.float32([kpa[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([kpb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
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
                    confidence = min(1.0, 0.35 + 0.5 * (inliers / max(30, len(good))) + 0.15 * max(0, 1 - residual / 10))

    if method == "identity" or confidence < min_conf:
        # ECC fallback
        try:
            ga_f = ga.astype(np.float32) / 255.0
            gb_f = gb.astype(np.float32) / 255.0
            warp = np.eye(2, 3, dtype=np.float32)
            criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-5)
            cc, warp = cv2.findTransformECC(gb_f, ga_f, warp, cv2.MOTION_AFFINE, criteria)
            matrix = warp.astype(np.float64)
            method = "ecc"
            confidence = float(max(confidence, min(1.0, cc)))
            residual = float(max(0.0, (1.0 - cc) * 10))
            inliers = max(inliers, 10)
        except Exception:
            pass

    # Convert pixel affine to normalized-space affine for [0,1] boxes.
    # x_n' = (a*(x_n*w) + b*(y_n*h) + tx)/w
    a, b, tx = matrix[0]
    c, d, ty = matrix[1]
    norm_matrix = [
        [float(a), float(b * h / max(w, 1)), float(tx / max(w, 1))],
        [float(c * w / max(h, 1)), float(d), float(ty / max(h, 1))],
    ]

    result = {
        "method": method,
        "inliers": inliers,
        "residual_error": round(residual, 4),
        "confidence": round(float(confidence), 4),
        "pixel_matrix": matrix.tolist(),
        "norm_matrix": norm_matrix,
        "image_size": [int(w), int(h)],
        "rejected": False,
    }
    if confidence < min_conf and method == "identity":
        result["rejected"] = True
        result["warning"] = "Registration quality below threshold; visual geometry diff suppressed"
    return result
