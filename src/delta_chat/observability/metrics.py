"""Metrics writer for metrics.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Metrics:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        base = {
            "stage_latency_ms": {},
            "counts": {},
            "errors": {},
            "llm": {
                "calls": 0,
                "total_tokens": 0,
                "total_cost": None,
                "cost_status": "unavailable",
                "cost_reason": "no_provider_pricing_table",
            },
        }
        if initial:
            # shallow merge known keys
            for k, v in initial.items():
                if isinstance(v, dict) and isinstance(base.get(k), dict):
                    base[k] = {**base[k], **v}
                else:
                    base[k] = v
        self.data: dict[str, Any] = base

    @classmethod
    def load(cls, path: Path) -> Metrics:
        if path.exists():
            try:
                return cls(json.loads(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                return cls()
        return cls()

    def set_stage(self, name: str, duration_ms: float) -> None:
        self.data["stage_latency_ms"][name] = duration_ms

    def incr(self, key: str, n: int = 1) -> None:
        self.data["counts"][key] = self.data["counts"].get(key, 0) + n

    def incr_error(self, error_type: str, n: int = 1) -> None:
        self.data.setdefault("errors", {})
        self.data["errors"][error_type] = self.data["errors"].get(error_type, 0) + n

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def merge_llm(
        self,
        *,
        calls: int,
        total_tokens: int,
        total_cost: float | None,
        cost_status: str,
        cost_reason: str,
    ) -> None:
        llm = self.data.setdefault("llm", {})
        llm["calls"] = int(llm.get("calls") or 0) + int(calls)
        llm["total_tokens"] = int(llm.get("total_tokens") or 0) + int(total_tokens)
        if total_cost is not None:
            prev = llm.get("total_cost")
            llm["total_cost"] = (float(prev) if prev is not None else 0.0) + float(total_cost)
            llm["cost_status"] = cost_status
            llm["cost_reason"] = cost_reason
        else:
            llm.setdefault("total_cost", None)
            llm["cost_status"] = cost_status
            llm["cost_reason"] = cost_reason

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.data, indent=2, default=str), encoding="utf-8")
