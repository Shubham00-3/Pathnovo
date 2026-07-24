"""Lightweight span tracing persisted as trace.json."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(
        self,
        path: Path,
        request_id: str,
        *,
        correlation_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> None:
        self.path = path
        self.request_id = request_id
        self.correlation_id = correlation_id or request_id
        self.parent_run_id = parent_run_id
        self.spans: list[dict[str, Any]] = []
        self._open: dict[str, dict[str, Any]] = {}
        # Load existing spans if appending to a run trace
        if path.exists():
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
                self.spans = list(prior.get("spans") or [])
            except Exception:  # noqa: BLE001
                self.spans = []

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        start = time.time()
        span: dict[str, Any] = {
            "name": name,
            "start": start,
            "status": "ok",
            "attributes": dict(attrs),
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
        }
        if self.parent_run_id:
            span["parent_run_id"] = self.parent_run_id
        self._open[name] = span
        try:
            yield span
        except Exception as exc:  # noqa: BLE001
            span["status"] = "error"
            span["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            raise
        finally:
            end = time.time()
            span["end"] = end
            span["duration_ms"] = round((end - start) * 1000, 2)
            self.spans.append(span)
            self._open.pop(name, None)

    def write(self) -> None:
        payload = {
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "parent_run_id": self.parent_run_id,
            "spans": self.spans,
            "total_duration_ms": round(sum(s.get("duration_ms", 0) for s in self.spans), 2),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
