"""Run context and request IDs."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from delta_chat.errors import DeltaChatError

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InvalidRequestIdError(DeltaChatError):
    code = "invalid_request_id"


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def validate_request_id(request_id: str) -> str:
    rid = (request_id or "").strip()
    if not REQUEST_ID_RE.fullmatch(rid):
        raise InvalidRequestIdError(
            "request_id must match [A-Za-z0-9_-]{1,64}",
            details={"request_id": request_id},
        )
    if rid in {".", ".."} or "/" in rid or "\\" in rid:
        raise InvalidRequestIdError(
            "request_id path traversal rejected", details={"request_id": request_id}
        )
    return rid


@dataclass
class RunContext:
    request_id: str
    correlation_id: str
    run_dir: Path
    started: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        artifacts_root: Path,
        request_id: str | None = None,
        *,
        correlation_id: str | None = None,
        allow_existing: bool = False,
    ) -> RunContext:
        rid = validate_request_id(request_id) if request_id else new_request_id()
        runs_root = (artifacts_root / "runs").resolve()
        runs_root.mkdir(parents=True, exist_ok=True)
        run_dir = (runs_root / rid).resolve()
        try:
            run_dir.relative_to(runs_root)
        except ValueError as exc:
            raise InvalidRequestIdError(
                "request_id resolves outside artifacts/runs",
                details={"request_id": rid, "path": str(run_dir)},
            ) from exc
        if run_dir.exists() and not allow_existing:
            raise InvalidRequestIdError(
                "request_id already exists",
                details={"request_id": rid},
            )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "crops").mkdir(exist_ok=True)
        return cls(
            request_id=rid,
            correlation_id=correlation_id or rid,
            run_dir=run_dir,
        )
