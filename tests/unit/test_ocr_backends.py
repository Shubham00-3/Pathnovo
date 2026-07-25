"""OCR backend seam: selection, fallback, and failure visibility."""

from __future__ import annotations

import numpy as np
import pytest

from delta_chat.errors import OcrFailure
from delta_chat.ingest.ocr import (
    BACKENDS,
    any_backend_available,
    available_backends,
    select_backend,
)
from delta_chat.ingest.ocr.base import OcrAvailability, OcrBackend, OcrWord


class _FakeBackend:
    """Stands in for a real engine so tests never depend on installed weights."""

    def __init__(self, name: str, available: bool, granularity: str = "word") -> None:
        self.name = name
        self.version = "test"
        self.granularity = granularity
        self._available = available

    def probe(self) -> OcrAvailability:
        return OcrAvailability(
            available=self._available,
            reason="" if self._available else "not installed",
            version="test",
        )

    def recognize(self, image: np.ndarray, *, config: dict) -> list[OcrWord]:
        return [OcrWord(text="HH", confidence=0.99, bbox_px=(0.0, 0.0, 10.0, 10.0))]


def test_registered_backends_satisfy_the_protocol():
    for backend in BACKENDS.values():
        assert isinstance(backend, OcrBackend)
        assert backend.granularity in {"word", "line"}


def test_auto_falls_through_to_the_first_available_backend(monkeypatch):
    monkeypatch.setitem(BACKENDS, "rapidocr", _FakeBackend("rapidocr", available=False))
    monkeypatch.setitem(BACKENDS, "tesseract", _FakeBackend("tesseract", available=True))

    backend, availability = select_backend({"ocr": {"backend": "auto"}})

    assert backend.name == "tesseract"
    assert availability.available


def test_auto_reports_every_candidate_when_none_are_available(monkeypatch):
    monkeypatch.setitem(BACKENDS, "rapidocr", _FakeBackend("rapidocr", available=False))
    monkeypatch.setitem(BACKENDS, "tesseract", _FakeBackend("tesseract", available=False))

    with pytest.raises(OcrFailure) as excinfo:
        select_backend({"ocr": {"backend": "auto"}})

    # A failure that names only one engine sends the reader down the wrong path.
    tried = excinfo.value.details["tried"]
    assert set(tried) == {"rapidocr", "tesseract"}
    assert excinfo.value.details["suggested_config"]


def test_explicit_backend_does_not_silently_fall_back(monkeypatch):
    """Asking for a specific engine and getting another would invalidate a comparison."""
    monkeypatch.setitem(BACKENDS, "tesseract", _FakeBackend("tesseract", available=False))
    monkeypatch.setitem(BACKENDS, "rapidocr", _FakeBackend("rapidocr", available=True))

    with pytest.raises(OcrFailure) as excinfo:
        select_backend({"ocr": {"backend": "tesseract"}})

    assert excinfo.value.details["requested"] == "tesseract"


def test_unknown_backend_name_is_rejected():
    with pytest.raises(OcrFailure) as excinfo:
        select_backend({"ocr": {"backend": "does-not-exist"}})

    assert "known_backends" in excinfo.value.details


def test_availability_probing_never_raises():
    """Probe runs on the health endpoint and in eval; it must not throw."""
    probed = available_backends({})
    assert probed
    assert all(isinstance(a, OcrAvailability) for a in probed.values())
    assert isinstance(any_backend_available({}), bool)


def test_priority_order_is_configurable(monkeypatch):
    monkeypatch.setitem(BACKENDS, "rapidocr", _FakeBackend("rapidocr", available=True))
    monkeypatch.setitem(BACKENDS, "tesseract", _FakeBackend("tesseract", available=True))

    backend, _ = select_backend(
        {"ocr": {"backend": "auto", "backend_priority": ["tesseract", "rapidocr"]}}
    )

    assert backend.name == "tesseract"
