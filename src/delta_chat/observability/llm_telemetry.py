"""LLM call telemetry to llm_calls.jsonl."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class LLMTelemetry:
    def __init__(self, path: Path, capture_content: bool = True) -> None:
        self.path = path
        self.capture_content = capture_content
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
        # Restore counters from existing log for cumulative metrics
        self.total_tokens = 0
        self.total_cost: float | None = None
        self.calls = 0
        self._restore()

    def _restore(self) -> None:
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                self.calls += 1
                self.total_tokens += int(rec.get("total_tokens") or 0)
                cost = rec.get("estimated_cost")
                if cost is not None:
                    self.total_cost = (self.total_cost or 0.0) + float(cost)
        except Exception:  # noqa: BLE001
            pass

    def record(self, **fields: Any) -> None:
        self.calls += 1
        tokens = int(fields.get("total_tokens") or 0)
        cost = fields.get("estimated_cost")
        self.total_tokens += tokens
        if cost is not None:
            self.total_cost = (self.total_cost or 0.0) + float(cost)

        prompt = fields.get("prompt")
        response = fields.get("response")
        if prompt is not None:
            fields["prompt_hash"] = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()[:16]
            fields["prompt_chars"] = len(str(prompt))
        if response is not None:
            fields["response_hash"] = hashlib.sha256(str(response).encode("utf-8")).hexdigest()[:16]
            fields["response_chars"] = len(str(response))

        if not self.capture_content:
            fields.pop("prompt", None)
            fields.pop("response", None)

        # Cost honesty: default unavailable unless provider sets estimated_cost
        if "estimated_cost" not in fields or fields.get("estimated_cost") is None:
            fields["estimated_cost"] = None
            fields["cost_status"] = fields.get("cost_status") or "unavailable"
            fields["cost_reason"] = fields.get("cost_reason") or "no_provider_pricing_table"

        record = {"ts": time.time(), **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
