from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from trueparse._version import get_version
from trueparse.contracts.document import Issue, Scope
from trueparse.models.assets import Asset
from trueparse.models.elements import GenericElement
from trueparse.models.enums import RelationshipType
from trueparse.models.quality import DocumentQuality, PageQuality


class Section(BaseModel):
    id: str
    title: str
    level: int = 1
    parent_id: str | None = None
    element_ids: list[str] = Field(default_factory=list)


class Relationship(BaseModel):
    id: str
    type: RelationshipType
    source_id: str
    target_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Page(BaseModel):
    page_number: int
    width: float
    height: float
    rotation: int = 0
    elements: list[GenericElement] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    quality: PageQuality = Field(default_factory=PageQuality)


class DocumentMetadata(BaseModel):
    title: str | None = None
    author: str | None = None
    creator: str | None = None
    producer: str | None = None
    creation_date: str | None = None
    modification_date: str | None = None
    page_count: int = 0
    file_size_bytes: int = 0
    sha256: str = ""


class Document(BaseModel):
    id: str
    engine_version: str = Field(default_factory=get_version)
    source_file: str | None = None
    metadata: DocumentMetadata | None = None
    pages: list[Page] = Field(default_factory=list)
    sections: list[Section] | None = None
    assets: dict[str, Asset] | None = None
    relationships: list[Relationship] | None = None
    quality: DocumentQuality | None = None
    warnings: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    scope: Scope = Field(
        default_factory=lambda: Scope(source_pages=0, requested_pages=[], processed_pages=[])
    )
    extraction_config: dict[str, Any] = Field(default_factory=dict, exclude=True)
