"""Tesseract OCR backend (requires the system `tesseract` binary on PATH)."""

from __future__ import annotations

import numpy as np

from delta_chat.errors import OcrFailure
from delta_chat.ingest.ocr.base import OcrAvailability, OcrWord


class TesseractBackend:
    name = "tesseract"
    version = "1.0.0"
    granularity = "word"

    def probe(self) -> OcrAvailability:
        try:
            import pytesseract

            version = str(pytesseract.get_tesseract_version())
        except Exception as exc:  # noqa: BLE001
            return OcrAvailability(
                available=False,
                reason=str(exc),
                details={"missing_dependency": "tesseract binary on PATH"},
            )
        return OcrAvailability(available=True, version=version)

    def recognize(self, image: np.ndarray, *, config: dict) -> list[OcrWord]:
        import pytesseract
        from pytesseract import Output

        ocr_cfg = config.get("ocr", {})
        lang = str(ocr_cfg.get("lang", "eng"))
        min_conf = float(ocr_cfg.get("min_confidence", 40))
        psm = ocr_cfg.get("psm")
        tess_config = f"--psm {int(psm)}" if psm is not None else ""
        max_words = int(config.get("max_ocr_words_per_page", 10_000))

        data = pytesseract.image_to_data(
            image, lang=lang, config=tess_config, output_type=Output.DICT
        )
        words: list[OcrWord] = []
        for i in range(len(data["text"])):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if conf < min_conf:
                continue
            x, y = float(data["left"][i]), float(data["top"][i])
            w, h = float(data["width"][i]), float(data["height"][i])
            words.append(
                OcrWord(
                    text=text,
                    # tesseract reports 0-100; normalize to 0-1
                    confidence=conf / 100.0 if conf > 1 else conf,
                    bbox_px=(x, y, x + w, y + h),
                )
            )
            if len(words) > max_words:
                raise OcrFailure(
                    "OCR output exceeds the configured word limit",
                    details={"max_ocr_words_per_page": max_words, "backend": self.name},
                )
        return words
