"""OCR backend registry.

`config.ocr.backend` selects an engine:
  auto       -- first available in `ocr.backend_priority` (default)
  rapidocr   -- ONNX, pip-only, no system binary
  tesseract  -- system binary, classic
Explicit names fail loudly when unavailable; `auto` fails only when every
candidate is unavailable, and the error names each one and why.
"""

from __future__ import annotations

from delta_chat.errors import OcrFailure
from delta_chat.ingest.ocr.base import OcrAvailability, OcrBackend, OcrWord
from delta_chat.ingest.ocr.rapid import RapidOcrBackend
from delta_chat.ingest.ocr.tesseract import TesseractBackend

BACKENDS: dict[str, OcrBackend] = {
    "rapidocr": RapidOcrBackend(),
    "tesseract": TesseractBackend(),
}

DEFAULT_PRIORITY = ["rapidocr", "tesseract"]

__all__ = [
    "BACKENDS",
    "DEFAULT_PRIORITY",
    "OcrAvailability",
    "OcrBackend",
    "OcrWord",
    "available_backends",
    "any_backend_available",
    "select_backend",
]


def _priority(config: dict) -> list[str]:
    ocr_cfg = config.get("ocr", {}) if config else {}
    names = ocr_cfg.get("backend_priority") or DEFAULT_PRIORITY
    return [str(n) for n in names if str(n) in BACKENDS]


def available_backends(config: dict | None = None) -> dict[str, OcrAvailability]:
    """Probe every candidate. Used by the eval harness and /api/health."""
    names = _priority(config or {}) or list(BACKENDS)
    return {name: BACKENDS[name].probe() for name in names}


def any_backend_available(config: dict | None = None) -> bool:
    return any(a.available for a in available_backends(config).values())


def select_backend(config: dict) -> tuple[OcrBackend, OcrAvailability]:
    """Resolve config to a usable backend, or raise OcrFailure naming every miss."""
    requested = str((config.get("ocr", {}) or {}).get("backend", "auto")).lower()

    if requested != "auto":
        backend = BACKENDS.get(requested)
        if backend is None:
            raise OcrFailure(
                f"Unknown OCR backend: {requested}",
                details={
                    "requested": requested,
                    "known_backends": sorted(BACKENDS),
                    "suggested_config": "Set ocr.backend to one of the known backends or 'auto'",
                },
            )
        availability = backend.probe()
        if not availability.available:
            raise OcrFailure(
                f"OCR backend '{requested}' is not available",
                details={
                    "requested": requested,
                    "reason": availability.reason,
                    **availability.details,
                    "suggested_config": "Set ocr.backend: auto to fall back automatically",
                },
            )
        return backend, availability

    probed = available_backends(config)
    for name, availability in probed.items():
        if availability.available:
            return BACKENDS[name], availability

    raise OcrFailure(
        "No OCR backend is available",
        details={
            "tried": {name: a.reason for name, a in probed.items()},
            "missing_dependency": "rapidocr-onnxruntime (pip) or tesseract (system binary)",
            "suggested_config": (
                "pip install 'delta-chat[ocr]' for the pip-only ONNX backend, "
                "or install Tesseract and put it on PATH"
            ),
        },
    )
