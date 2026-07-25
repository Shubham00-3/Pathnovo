"""Content-based format detection (not extension-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from delta_chat.errors import CorruptDocumentError, UnsupportedFormatError


def _read_magic(path: Path, n: int = 16) -> bytes:
    with path.open("rb") as f:
        return f.read(n)


DXF_BINARY_SENTINEL = b"AutoCAD Binary DXF"


def _looks_like_dxf(path: Path, magic: bytes) -> bool:
    """Content sniff for ASCII and binary DXF (neither has a magic number)."""
    if magic.startswith(DXF_BINARY_SENTINEL[: len(magic)]):
        return True
    try:
        with path.open("rb") as f:
            head = f.read(2048)
    except OSError:
        return False
    if head.startswith(DXF_BINARY_SENTINEL):
        return True
    # ASCII DXF: group code 0 followed by SECTION, within the first few lines.
    normalized = head.replace(b"\r\n", b"\n").lstrip()
    if normalized.startswith(b"0\nSECTION") or b"\n0\nSECTION\n" in normalized[:512]:
        return True
    return path.suffix.lower() == ".dxf" and b"SECTION" in head


def detect_format(path: Path | str, config: dict | None = None) -> dict[str, Any]:
    """Return detector signals and a chosen adapter name."""
    cfg = config or {}
    det = cfg.get("detection", {})
    p = Path(path)
    if not p.exists():
        raise CorruptDocumentError(f"File not found: {p}")
    file_size = p.stat().st_size
    max_file_bytes = int(cfg.get("max_file_bytes", 100 * 1024 * 1024))
    if file_size > max_file_bytes:
        raise CorruptDocumentError(
            f"Document exceeds the configured {max_file_bytes}-byte limit",
            details={"path": str(p), "byte_size": file_size, "max_file_bytes": max_file_bytes},
        )

    magic = _read_magic(p)
    signals: dict[str, Any] = {
        "path": str(p),
        "magic_hex": magic.hex(),
        "suffix": p.suffix.lower(),
        "size": file_size,
    }

    # DWG magic AC10xx -- routed to the CAD adapter, which converts to DXF.
    if magic.startswith(b"AC10"):
        signals.update(
            {
                "format_family": "dwg",
                "adapter": "dxf",
                "is_pdf": False,
                "reason": "DWG magic AC10xx",
            }
        )
        return signals

    # DXF has no magic number. ASCII DXF opens with a SECTION group code; binary
    # DXF carries a sentinel string. Check content first and fall back to suffix.
    if _looks_like_dxf(p, magic):
        signals.update(
            {
                "format_family": "dxf",
                "adapter": "dxf",
                "is_pdf": False,
                "reason": "DXF SECTION header or binary DXF sentinel",
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
            max_pages = int(cfg.get("max_pages", 20))
            if page_count > max_pages:
                raise CorruptDocumentError(
                    f"Document has {page_count} pages; configured limit is {max_pages}",
                    details={"path": str(p), "page_count": page_count, "max_pages": max_pages},
                )
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
