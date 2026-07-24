"""Ingestion adapters and factory."""

from __future__ import annotations

from pathlib import Path

from delta_chat.canonical.models import DocumentRevision
from delta_chat.errors import UnsupportedFormatError
from delta_chat.ingest.detector import detect_format
from delta_chat.ingest.dwg import DwgAdapter
from delta_chat.ingest.native_pdf import NativePdfAdapter
from delta_chat.ingest.scanned_pdf import ScannedPdfAdapter
from delta_chat.pid.models import ResolvedDocument

ADAPTERS = {
    "native_pdf": NativePdfAdapter(),
    "scanned_pdf": ScannedPdfAdapter(),
    "dwg": DwgAdapter(),
}


def get_adapter(name: str):
    if name not in ADAPTERS:
        raise UnsupportedFormatError(f"No adapter registered: {name}")
    return ADAPTERS[name]


def ingest_document(
    resolved: ResolvedDocument,
    *,
    out_dir: Path,
    config: dict,
    force_adapter: str | None = None,
) -> tuple[DocumentRevision, dict]:
    signals = detect_format(resolved.path, config)
    adapter_name = force_adapter or signals.get("adapter")
    adapter = get_adapter(adapter_name)
    revision = adapter.ingest(resolved, out_dir=out_dir, config=config)
    return revision, signals
