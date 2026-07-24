"""Structured delta models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ChangeType = Literal["added", "removed", "modified", "moved", "moved_modified"]


class DeltaItem(BaseModel):
    delta_item_id: str
    change_type: ChangeType
    entity_type: str
    page_a: int | None = None
    page_b: int | None = None
    region: dict[str, Any] = Field(default_factory=dict)
    before: str | None = None
    after: str | None = None
    before_ref: dict[str, Any] | None = None
    after_ref: dict[str, Any] | None = None
    deterministic_description: str
    optional_llm_description: str | None = None
    confidence: float
    confidence_band: str
    confidence_factors: dict[str, float] = Field(default_factory=dict)
    match_features: dict[str, Any] = Field(default_factory=dict)
    review_required: bool = False


class DeltaReport(BaseModel):
    schema_version: str = "1.0.0"
    delta_id: str
    pid_a: str
    pid_b: str
    pair_compatibility: dict[str, Any]
    config_hash: str
    generated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    summary: dict[str, Any] = Field(default_factory=dict)
    changes: list[DeltaItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    registration: dict[str, Any] = Field(default_factory=dict)
    page_alignment: dict[str, Any] = Field(default_factory=dict)
