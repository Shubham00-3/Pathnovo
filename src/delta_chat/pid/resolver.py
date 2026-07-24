"""PID resolver protocol."""

from __future__ import annotations

from typing import Protocol

from delta_chat.pid.models import ResolvedDocument


class PidResolver(Protocol):
    def resolve(self, pid: str) -> ResolvedDocument: ...

    def list_pids(self) -> list[str]: ...
