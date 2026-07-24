"""Content-based format detection (not extension-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from delta_chat.errors import CorruptDocumentError, UnsupportedFormatError


def _read_magic(path: Path, n: int = 16) -> bytes:
    with path.open("rb") as f:
        return f.read(n)


def detect_format(path: Path | str, config: dict | None = None) -> dict[str, Any]:
    """Return detector signals and a chosen adapter name."""
    cfg = config or {}
    det = cfg.get("detection", {})
    p = Path(path)
    if not p.exists():
        raise CorruptDocumentError(f"File not found: {p}")

    magic = _read_magic(p)
    signals: dict[str, Any] = {
        "path": str(p),
        "magic_hex": magic.hex(),
        "suffix": p.suffix.lower(),
        "size": p.stat().st_size,
    }

    # DWG magic AC10xx
    if magic.startswith(b"AC10"):
        signals.update(
            {
                "format_family": "dwg",
                "adapter": "dwg",
                "is_pdf": False,
                "reason": "DWG magic AC10xx",
            }
        )
        return signals

    # PDF
    if magic.startswith(b"%PDF") or p.suffix.lower() == ".pdf":
        try:
            doc = fitz.open(p)
        except Exception as exc:  # noqa: BLE001
            raise CorruptDocumentError(
                f"Corrupt or unreadable PDF: {p}",
                details={"error": str(exc)},
            ) from exc
        try:
            page_count = doc.page_count
            text_chars = 0
            vector_count = 0
            image_area = 0.0
            page_area = 0.0
            for i, page in enumerate(doc):
                if i >= 3:
                    break
                text_chars += len(page.get_text("text") or "")
                drawings = page.get_drawings() or []
                vector_count += len(drawings)
                rect = page.rect
                page_area += float(rect.width * rect.height)
                for img in page.get_images(full=True) or []:
                    # rough image presence signal
                    image_area += float(rect.width * rect.height) * 0.5
            image_coverage = (image_area / page_area) if page_area else 0.0
            # Better image coverage: check for full-page images
            full_page_images = 0
            for i, page in enumerate(doc):
                if i >= 3:
                    break
                blocks = page.get_text("dict").get("blocks", [])
                image_blocks = [b for b in blocks if b.get("type") == 1]
                if image_blocks and text_chars < 40:
                    full_page_images += 1
            if full_page_images and text_chars < det.get("min_native_text_chars", 80):
                image_coverage = max(image_coverage, 0.9)
        finally:
            doc.close()

        signals.update(
            {
                "format_family": "pdf",
                "is_pdf": True,
                "page_count": page_count,
                "text_chars_sample": text_chars,
                "vector_count_sample": vector_count,
                "image_coverage_est": round(image_coverage, 3),
            }
        )
        min_text = det.get("min_native_text_chars", 80)
        min_vec = det.get("min_vector_objects", 20)
        max_img = det.get("max_image_coverage", 0.55)
        if text_chars >= min_text or vector_count >= min_vec:
            if image_coverage >= max_img and text_chars < min_text:
                signals["adapter"] = "scanned_pdf"
                signals["reason"] = "high image coverage, low native text"
            else:
                signals["adapter"] = "native_pdf"
                signals["reason"] = "positioned text/vector density"
        else:
            signals["adapter"] = "scanned_pdf"
            signals["reason"] = "insufficient native text; route to OCR"
        return signals

    raise UnsupportedFormatError(
        f"Unrecognized document format for {p.name}",
        details={"magic_hex": magic.hex(), "suffix": p.suffix},
    )
