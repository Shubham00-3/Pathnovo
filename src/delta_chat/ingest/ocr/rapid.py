"""RapidOCR (ONNX Runtime) backend -- pip-installable, no system binary.

This is the default because a reviewer running `pip install -e .` gets a working
scanned-PDF path on any OS. Tesseract stays available for comparison and because
it is the incumbent in most document pipelines.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from delta_chat.errors import OcrFailure
from delta_chat.ingest.ocr.base import OcrAvailability, OcrWord

# Model init loads three ONNX graphs (~15MB); build once per process.
_ENGINE: Any = None
_ENGINE_LOCK = threading.Lock()


def _get_engine() -> Any:
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                from rapidocr_onnxruntime import RapidOCR

                _ENGINE = RapidOCR()
    return _ENGINE


def _quad_to_bbox(box: Any) -> tuple[float, float, float, float]:
    pts = np.asarray(box, dtype=np.float64).reshape(-1, 2)
    return (
        float(pts[:, 0].min()),
        float(pts[:, 1].min()),
        float(pts[:, 0].max()),
        float(pts[:, 1].max()),
    )


class RapidOcrBackend:
    name = "rapidocr"
    version = "1.0.0"
    # DB text detection already returns line-level regions.
    granularity = "line"

    def probe(self) -> OcrAvailability:
        try:
            import rapidocr_onnxruntime
        except Exception as exc:  # noqa: BLE001
            return OcrAvailability(
                available=False,
                reason=str(exc),
                details={"missing_dependency": "pip install rapidocr-onnxruntime"},
            )

        version = getattr(rapidocr_onnxruntime, "__version__", None)
        if not version:
            # The package exposes no __version__; fall back to installed metadata
            # so the scorecard records which build produced a result.
            from importlib.metadata import PackageNotFoundError
            from importlib.metadata import version as pkg_version

            try:
                version = pkg_version("rapidocr-onnxruntime")
            except PackageNotFoundError:
                version = "unknown"
        return OcrAvailability(available=True, version=str(version))

    def recognize(self, image: np.ndarray, *, config: dict) -> list[OcrWord]:
        ocr_cfg = config.get("ocr", {})
        # Tesseract confidence is 0-100 in config; normalize once here.
        raw_min = float(ocr_cfg.get("min_confidence", 40))
        min_conf = raw_min / 100.0 if raw_min > 1 else raw_min
        max_words = int(config.get("max_ocr_words_per_page", 10_000))

        # RapidOCR expects 3-channel input; grayscale pages come from preprocessing.
        img = image
        if img.ndim == 2:
            img = np.stack([img] * 3, axis=-1)

        try:
            result = _get_engine()(img)
        except Exception as exc:  # noqa: BLE001
            raise OcrFailure(
                "RapidOCR inference failed",
                details={"backend": self.name, "error": str(exc)},
            ) from exc

        # rapidocr returns (detections, timings); newer builds may return an object.
        detections = result[0] if isinstance(result, tuple) else getattr(result, "boxes", result)
        if not detections:
            return []

        words: list[OcrWord] = []
        for det in detections:
            try:
                box, text, score = det[0], det[1], det[2]
            except (TypeError, IndexError, KeyError):
                continue
            text = str(text or "").strip()
            if not text:
                continue
            conf = float(score)
            if conf < min_conf:
                continue
            words.append(OcrWord(text=text, confidence=conf, bbox_px=_quad_to_bbox(box)))
            if len(words) > max_words:
                raise OcrFailure(
                    "OCR output exceeds the configured word limit",
                    details={"max_ocr_words_per_page": max_words, "backend": self.name},
                )
        return words
