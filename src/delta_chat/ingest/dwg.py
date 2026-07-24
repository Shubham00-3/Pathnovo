"""DWG adapter seam: detect and fail visibly unless a converter is configured."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from delta_chat.canonical.models import DocumentRevision
from delta_chat.errors import UnsupportedFormatError
from delta_chat.pid.models import ResolvedDocument

ADAPTER_NAME = "dwg"
ADAPTER_VERSION = "1.0.0"


class DwgAdapter:
    name = ADAPTER_NAME
    version = ADAPTER_VERSION

    def supports(self, path: Path, signals: dict) -> bool:
        return signals.get("adapter") == self.name or signals.get("format_family") == "dwg"

    def ingest(
        self,
        resolved: ResolvedDocument,
        *,
        out_dir: Path,
        config: dict,
    ) -> DocumentRevision:
        converter = (
            config.get("dwg", {}).get("converter_path")
            or os.environ.get("DWG_CONVERTER_PATH")
            or ""
        )
        if converter and Path(converter).exists():
            # Optional conversion path: convert to PDF then refuse to claim full E2E.
            # Kept minimal and explicit.
            out_pdf = out_dir / f"{resolved.pid}_converted.pdf"
            try:
                subprocess.run(
                    [converter, str(resolved.path), str(out_pdf)],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
            except Exception as exc:  # noqa: BLE001
                raise UnsupportedFormatError(
                    "DWG converter failed",
                    details={
                        "pid": resolved.pid,
                        "detected_format": "dwg",
                        "missing_dependency": None,
                        "converter": converter,
                        "error": str(exc),
                        "suggested_configuration": "Set DWG_CONVERTER_PATH to a working ODA/AutoCAD converter",
                    },
                ) from exc
            raise UnsupportedFormatError(
                "DWG conversion produced a PDF intermediate, but full DWG semantics are not end-to-end in this build",
                details={
                    "pid": resolved.pid,
                    "detected_format": "dwg",
                    "converted_pdf": str(out_pdf),
                    "suggested_configuration": "Implement DXF entity mapping for end-to-end DWG",
                },
            )

        which = shutil.which("odafileconverter") or shutil.which("dwg2dxf")
        raise UnsupportedFormatError(
            "DWG detected but no converter is configured",
            details={
                "pid": resolved.pid,
                "detected_format": "dwg",
                "missing_dependency": "ODA File Converter or AutoCAD DWG converter",
                "found_on_path": which,
                "suggested_configuration": (
                    "Install a DWG converter and set DWG_CONVERTER_PATH, "
                    "or export drawings to PDF for native/scanned paths"
                ),
            },
        )
