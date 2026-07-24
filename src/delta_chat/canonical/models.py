"""Versioned, format-agnostic canonical document models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ElementKind = Literal[
    "text",
    "note",
    "equipment_tag",
    "instrument_tag",
    "line_tag",
    "dimension",
    "table_cell",
    "symbol",
    "geometry_cluster",
    "image_region",
    "markup_cloud",
]

SCHEMA_VERSION = "1.0.0"


class SourceRef(BaseModel):
    pid: str
    page_number: int = 1
    sheet_id: str | None = None
    grid_region: str | None = None
    bbox: list[float] = Field(default_factory=list)
    element_ids: list[str] = Field(default_factory=list)
    quote: str | None = None


class CanonicalElement(BaseModel):
    element_id: str
    kind: ElementKind = "text"
    raw_text: str = ""
    normalized_text: str = ""
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    polygon: list[list[float]] | None = None
    rotation_degrees: float = 0.0
    style: dict[str, Any] = Field(default_factory=dict)
    identifiers: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    extraction_confidence: float = 1.0
    source_ref: SourceRef | None = None
    neighbors: list[str] = Field(default_factory=list)


class CanonicalPage(BaseModel):
    page_number: int
    sheet_id: str | None = None
    width: float
    height: float
    coordinate_space: str = "normalized_top_left"
    render_path: str | None = None
    page_text: str = ""
    elements: list[CanonicalElement] = Field(default_factory=list)
    extraction_metrics: dict[str, Any] = Field(default_factory=dict)


class DocumentRevision(BaseModel):
    schema_version: str = SCHEMA_VERSION
    pid: str
    underlying_document_id: str
    revision_label: str
    source_format: str
    source_sha256: str
    adapter_name: str
    adapter_version: str = "1.0.0"
    pages: list[CanonicalPage] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
