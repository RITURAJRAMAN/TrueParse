from __future__ import annotations

from pydantic import BaseModel, Field

from trueparse.models.elements import SourceProvenance
from trueparse.models.enums import AssetType
from trueparse.models.geometry import BoundingBox


class AssetOccurrence(BaseModel):
    page: int
    bbox: BoundingBox | None = None


class Asset(BaseModel):
    id: str
    type: AssetType
    path: str | None = None
    data_uri: str | None = None
    mime_type: str
    sha256: str
    width: int
    height: int
    occurrences: list[AssetOccurrence] = Field(default_factory=list)
    source: SourceProvenance = Field(default_factory=SourceProvenance)
