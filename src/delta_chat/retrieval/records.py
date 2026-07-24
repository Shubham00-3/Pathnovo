"""Retrieval record model."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SourceFamily = Literal["rev_a", "rev_b", "delta"]


class RetrievalRecord(BaseModel):
    source_id: str
    source_family: SourceFamily
    pid: str | None = None
    delta_id: str | None = None
    page: int | None = None
    sheet_id: str | None = None
    grid_region: str | None = None
    bbox: list[float] = Field(default_factory=list)
    text: str = ""
    identifiers: list[str] = Field(default_factory=list)
    entity_type: str = "text"
    confidence: float = 1.0
    meta: dict[str, Any] = Field(default_factory=dict)
