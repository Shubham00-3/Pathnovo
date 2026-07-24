"""PID resolution models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ResolvedDocument(BaseModel):
    pid: str
    underlying_document_id: str
    revision_label: str = "A"
    display_name: str = ""
    media_type: str = "application/pdf"
    source_uri: str
    local_path: str
    byte_size: int = 0
    sha256: str = ""
    sheet_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def path(self) -> Path:
        return Path(self.local_path)
