"""OCR backend seam.

The scanned-PDF adapter must not be welded to one OCR engine. Tesseract needs a
system binary that a reviewer may not have; RapidOCR ships ONNX weights via pip
and runs anywhere. Both produce the same thing -- words with pixel boxes and a
confidence -- so they sit behind one protocol and the adapter stays unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class OcrWord:
    """One recognized token with its pixel-space box on the rendered page."""

    text: str
    confidence: float
    bbox_px: tuple[float, float, float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "conf": self.confidence,
            "bbox_px": list(self.bbox_px),
        }


@dataclass(frozen=True)
class OcrAvailability:
    """Result of probing a backend without running it."""

    available: bool
    reason: str = ""
    version: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class OcrBackend(Protocol):
    """Implement this to plug a new OCR engine into the scanned adapter."""

    name: str
    version: str
    # "word": engine emits individual tokens, the adapter must group them into
    # lines. "line": engine already emits line regions -- re-grouping them is
    # actively harmful, because a band boundary that falls differently on Rev A
    # than Rev B invents added/removed pairs out of identical content.
    granularity: str

    def probe(self) -> OcrAvailability:
        """Cheap availability check. Must not raise."""
        ...

    def recognize(
        self,
        image: np.ndarray,
        *,
        config: dict,
    ) -> list[OcrWord]:
        """Recognize words in a grayscale or BGR page image."""
        ...
