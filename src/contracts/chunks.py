from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChunkSourceSpan(BaseModel):
    element_id: str
    source_element_id: str | None = None
    page: int
    bbox: list[float] | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    row_start: int | None = Field(default=None, ge=0)
    row_end: int | None = Field(default=None, ge=0)
    representation: str = "content"


class Chunk(BaseModel):
    """A retrieval-ready slice of a document, carrying full spatial provenance.

    Every field beyond ``text`` exists so a downstream RAG answer can be cited
    back to an exact region of the source PDF.
    """

    id: str
    document_id: str
    chunk_index: int = 0
    text: str
    token_estimate: int = 0
    tokenizer: str = "approximate"
    budget_exceeded: bool = False
    source_spans: list[ChunkSourceSpan] = Field(default_factory=list)
    section_id: str | None = None
    section_path: list[str] = Field(
        default_factory=list,
        description="Heading breadcrumb from document root down to this chunk",
    )
    page_start: int = 0
    page_end: int = 0
    bboxes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-element {page, bbox} pairs covering this chunk",
    )
    element_ids: list[str] = Field(default_factory=list)
    element_types: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
