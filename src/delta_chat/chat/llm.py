"""Swappable LLM clients. Extractive is default (no API key)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol

from delta_chat.errors import LLMTimeoutError
from delta_chat.observability.llm_telemetry import LLMTelemetry


class LLMClient(Protocol):
    provider: str

    def answer(self, prompt: str, *, system: str = "") -> dict[str, Any]: ...


class ExtractiveLLMClient:
    """Deterministic no-key baseline: extractive answer from evidence."""

    provider = "extractive"

    def __init__(self, telemetry: LLMTelemetry | None = None) -> None:
        self.telemetry = telemetry

    def answer(self, prompt: str, *, system: str = "") -> dict[str, Any]:
        start = time.time()
        # evidence blocks marked with [source_id]
        blocks = re.findall(r"\[(D:[^\]]+|A:[^\]]+|B:[^\]]+)\]\s*(.+?)(?=\n\[|\Z)", prompt, re.S)
        citations = []
        snippets = []
        for sid, text in blocks[:6]:
            citations.append(sid.strip())
            snippets.append(text.strip().splitlines()[0][:240])
        qmatch = re.search(r"Question:\s*(.+)", prompt)
        question = qmatch.group(1).strip() if qmatch else ""
        ql = question.lower()

        if not snippets:
            result = {
                "answer": (
                    "I could not find enough evidence in the provided PIDs or delta report to answer that."
                ),
                "citations": [],
                "confidence": "low",
                "unsupported": True,
            }
        elif any(w in ql for w in ("summarize", "high-confidence", "high confidence")):
            result = {
                "answer": "High-confidence / retrieved changes: " + " | ".join(snippets[:5]),
                "citations": citations[:5],
                "confidence": "high",
                "unsupported": False,
            }
        elif "how many" in ql and "added" in ql:
            # count from evidence lines mentioning added
            n = sum(1 for s in snippets if "added" in s.lower())
            result = {
                "answer": f"Based on retrieved delta evidence, about {n} added items appear in the top evidence set.",
                "citations": citations[:3] or citations,
                "confidence": "medium",
                "unsupported": False,
            }
        else:
            result = {
                "answer": snippets[0],
                "citations": citations[:3],
                "confidence": "medium",
                "unsupported": False,
            }

        latency = (time.time() - start) * 1000
        if self.telemetry:
            self.telemetry.record(
                provider=self.provider,
                model="extractive",
                temperature=0,
                prompt=prompt[:2000],
                response=json.dumps(result),
                prompt_tokens=len(prompt.split()),
                completion_tokens=len(result["answer"].split()),
                total_tokens=len(prompt.split()) + len(result["answer"].split()),
                estimated_cost=None,
                cost_status="unavailable",
                cost_reason="extractive_provider_has_no_token_pricing",
                latency_ms=round(latency, 2),
                status="ok",
            )
        return result


class FakeLLMClient:
    provider = "fake"

    def __init__(
        self, response: dict[str, Any] | None = None, telemetry: LLMTelemetry | None = None
    ) -> None:
        self.response = response or {
            "answer": "fake",
            "citations": [],
            "confidence": "low",
            "unsupported": True,
        }
        self.telemetry = telemetry

    def answer(self, prompt: str, *, system: str = "") -> dict[str, Any]:
        if self.telemetry:
            self.telemetry.record(
                provider="fake",
                model="fake",
                prompt=prompt[:500],
                response=json.dumps(self.response),
                total_tokens=10,
                estimated_cost=0.0,
                latency_ms=1,
                status="ok",
            )
        return dict(self.response)


class LiteLLMClient:
    provider = "litellm"

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 800,
        telemetry: LLMTelemetry | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.telemetry = telemetry
        self.timeout_s = timeout_s
        self.provider = "litellm"

    def answer(self, prompt: str, *, system: str = "") -> dict[str, Any]:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover
            raise LLMTimeoutError(
                "litellm is not installed; pip install 'delta-chat[llm]' or use LLM_PROVIDER=extractive",
                details={"missing_dependency": "litellm"},
            ) from exc

        start = time.time()
        try:
            resp = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system or "Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            if self.telemetry:
                self.telemetry.record(
                    provider="litellm",
                    model=self.model,
                    status="error",
                    error=str(exc),
                    latency_ms=round((time.time() - start) * 1000, 2),
                )
            raise LLMTimeoutError(str(exc), details={"model": self.model}) from exc

        content = resp.choices[0].message.content or "{}"
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total = prompt_tokens + completion_tokens
        # Cost: use provider usage if available; otherwise mark unavailable (no guessed pricing)
        cost = None
        cost_status = "unavailable"
        cost_reason = "no_provider_pricing_table"
        try:
            hidden = getattr(resp, "_hidden_params", None) or {}
            if isinstance(hidden, dict) and hidden.get("response_cost") is not None:
                cost = float(hidden["response_cost"])
                cost_status = "provider"
                cost_reason = "litellm_response_cost"
        except Exception:  # noqa: BLE001
            pass
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Do not invent citations for raw text; let chat service refuse/retry
            data = {
                "answer": content,
                "citations": [],
                "confidence": "low",
                "unsupported": True,
            }
        if self.telemetry:
            self.telemetry.record(
                provider="litellm",
                model=self.model,
                temperature=self.temperature,
                prompt=prompt,
                response=content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total,
                estimated_cost=cost,
                cost_status=cost_status,
                cost_reason=cost_reason,
                latency_ms=round((time.time() - start) * 1000, 2),
                status="ok",
            )
        return data


def build_llm_client(config: dict, telemetry: LLMTelemetry | None = None) -> Any:
    lcfg = config.get("llm", {})
    provider = (os.environ.get("LLM_PROVIDER") or lcfg.get("provider") or "extractive").lower()
    model = os.environ.get("LLM_MODEL") or lcfg.get("model") or ""
    if provider in {"extractive", "none", "deterministic"}:
        return ExtractiveLLMClient(telemetry=telemetry)
    if provider == "fake":
        return FakeLLMClient(telemetry=telemetry)
    if not model:
        return ExtractiveLLMClient(telemetry=telemetry)
    return LiteLLMClient(
        model=model,
        temperature=float(lcfg.get("temperature", 0.0)),
        max_tokens=int(lcfg.get("max_tokens", 800)),
        telemetry=telemetry,
    )
