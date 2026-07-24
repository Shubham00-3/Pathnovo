"""Metrics writer for metrics.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Metrics:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {
            "stage_latency_ms": {},
            "counts": {},
            "llm": {},
            "errors": {},
        }

    def set_stage(self, name: str, duration_ms: float) -> None:
        self.data["stage_latency_ms"][name] = duration_ms

    def incr(self, key: str, n: int = 1) -> None:
        self.data["counts"][key] = self.data["counts"].get(key, 0) + n

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.data, indent=2, default=str), encoding="utf-8")
