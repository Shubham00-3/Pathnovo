"""Local JSON-backed PID registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from delta_chat.config import project_root, resolve_under_root
from delta_chat.errors import PidNotFoundError
from delta_chat.pid.models import ResolvedDocument


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalRegistryResolver:
    def __init__(self, registry_path: str | Path = "data/registry.json") -> None:
        root = project_root()
        self.root = root
        rp = Path(registry_path)
        # Registry metadata file may be absolute (tests); document paths are still constrained.
        if rp.is_absolute():
            self.registry_path = rp
        else:
            self.registry_path = resolve_under_root(registry_path, root=root)
        self._entries: dict[str, dict[str, Any]] = {}
        if self.registry_path.exists():
            with self.registry_path.open(encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise PidNotFoundError("Registry root must be an object")
            self._entries = data

    def list_pids(self) -> list[str]:
        return sorted(self._entries.keys())

    def resolve(self, pid: str) -> ResolvedDocument:
        if pid not in self._entries:
            raise PidNotFoundError(
                f"PID not found: {pid}",
                details={"pid": pid, "known": self.list_pids()},
            )
        entry = self._entries[pid]
        rel = entry.get("path")
        if not rel:
            raise PidNotFoundError(f"PID {pid} has no path", details={"pid": pid})
        local = resolve_under_root(rel, root=self.root)
        if not local.exists():
            raise PidNotFoundError(
                f"Document file missing for {pid}: {local}",
                details={"pid": pid, "path": str(local)},
            )
        byte_size = local.stat().st_size
        if byte_size > int(entry.get("max_bytes", 100 * 1024 * 1024)):
            raise PidNotFoundError(f"File too large for {pid}")
        return ResolvedDocument(
            pid=pid,
            underlying_document_id=entry.get("underlying_document_id", pid),
            revision_label=str(entry.get("revision_label", "A")),
            display_name=entry.get("display_name") or local.name,
            media_type=entry.get("media_type", "application/pdf"),
            source_uri=f"file://{local.as_posix()}",
            local_path=str(local),
            byte_size=byte_size,
            sha256=_sha256_file(local),
            sheet_count=entry.get("sheet_count"),
            metadata={k: v for k, v in entry.items() if k not in {"path"}},
        )
