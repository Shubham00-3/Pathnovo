"""Chat request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Citation(BaseModel):
    source_id: str
    source_family: str | None = None
    pid: str | None = None
    page: int | None = None
    grid_region: str | None = None
    quote: str | None = None
    bbox: list[float] = Field(default_factory=list)


class ChatAnswer(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: str = "medium"
    unsupported: bool = False
    route: str | None = None
    retrieval: list[dict[str, Any]] = Field(default_factory=list)
    provider: str = "extractive"
