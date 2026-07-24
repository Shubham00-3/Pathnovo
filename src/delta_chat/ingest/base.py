"""Format adapter protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from delta_chat.canonical.models import DocumentRevision
from delta_chat.pid.models import ResolvedDocument


class FormatAdapter(Protocol):
    name: str
    version: str

    def supports(self, path: Path, signals: dict) -> bool: ...

    def ingest(
        self,
        resolved: ResolvedDocument,
        *,
        out_dir: Path,
        config: dict,
    ) -> DocumentRevision: ...
