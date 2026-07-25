"""Create scanned (image-only) variants of the synthetic pair."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np

from delta_chat.config import project_root


def _rasterize_degrade(pdf_path: Path, out_pdf: Path, seed: int, skew: float = 0.8) -> None:
    rng = np.random.default_rng(seed)
    doc = fitz.open(pdf_path)
    out = fitz.open()
    for i in range(doc.page_count):
        page = doc[i]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        img: Any = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        # mild blur
        img = cv2.GaussianBlur(img, (3, 3), 0.6)
        # brightness
        img = cv2.convertScaleAbs(img, alpha=1.0, beta=int(rng.integers(-12, 12)))
        # skew
        h, w = img.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), skew, 1.0)
        img = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
        # JPEG artifacts via encode/decode
        ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 55])
        if ok:
            img = cv2.imdecode(enc, cv2.IMREAD_COLOR)
        # optional speckle
        noise = rng.normal(0, 4, img.shape).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        # image-only PDF page
        # use JPEG bytes
        ok, jpg = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        img_bytes = jpg.tobytes() if ok else pix.tobytes()
        # page size matches original points
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        rect = new_page.rect
        new_page.insert_image(rect, stream=img_bytes)
    out.save(str(out_pdf))
    out.close()
    doc.close()


def main(seed: int = 42) -> dict:
    root = project_root()
    src = root / "data" / "samples" / "synthetic_native"
    out = root / "data" / "samples" / "synthetic_scanned"
    out.mkdir(parents=True, exist_ok=True)
    a_src = src / "lift_rev_a.pdf"
    b_src = src / "lift_rev_b.pdf"
    if not a_src.exists() or not b_src.exists():
        from scripts.make_synthetic_pid_pair import main as make_syn

        make_syn(seed=seed)

    path_a = out / "lift_rev_a_scan.pdf"
    path_b = out / "lift_rev_b_scan.pdf"
    _rasterize_degrade(a_src, path_a, seed=seed, skew=0.6)
    _rasterize_degrade(b_src, path_b, seed=seed + 1, skew=-0.5)

    # reuse GT with note about tolerance
    gt_src = src / "ground_truth.json"
    gt = json.loads(gt_src.read_text(encoding="utf-8")) if gt_src.exists() else {}
    gt_scan = {
        **gt,
        "pid_a": "PID-SYN-SCAN-A",
        "pid_b": "PID-SYN-SCAN-B",
        "scan_settings": {
            "skew_deg": [0.6, -0.5],
            "jpeg_quality": 55,
            "blur": True,
            "brightness_jitter": True,
            "speckle": True,
        },
        "location_tolerance": 0.08,
        "source_native_gt": str(gt_src.as_posix()),
    }
    (out / "ground_truth.json").write_text(json.dumps(gt_scan, indent=2), encoding="utf-8")
    print(f"Wrote {path_a}")
    print(f"Wrote {path_b}")
    return gt_scan


if __name__ == "__main__":
    main()
