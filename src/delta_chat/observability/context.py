"""Run context and request IDs."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class RunContext:
    request_id: str
    correlation_id: str
    run_dir: Path
    started: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, artifacts_root: Path, request_id: str | None = None) -> RunContext:
        rid = request_id or new_request_id()
        run_dir = artifacts_root / "runs" / rid
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "crops").mkdir(exist_ok=True)
        return cls(request_id=rid, correlation_id=rid, run_dir=run_dir)
