"""Lightweight span tracing persisted as trace.json."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(self, path: Path, request_id: str) -> None:
        self.path = path
        self.request_id = request_id
        self.spans: list[dict[str, Any]] = []
        self._open: dict[str, dict[str, Any]] = {}

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        start = time.time()
        span: dict[str, Any] = {
            "name": name,
            "start": start,
            "status": "ok",
            "attributes": dict(attrs),
        }
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
            "spans": self.spans,
            "total_duration_ms": round(
                sum(s.get("duration_ms", 0) for s in self.spans), 2
            ),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
