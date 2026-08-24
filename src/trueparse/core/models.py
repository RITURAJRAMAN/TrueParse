from __future__ import annotations
from typing import Any, Optional, Union
from pydantic import BaseModel, Field

from trueparse.core.enums import (
    ElementType,
    AssetType,
    SourceMethod,
    RelationshipType,
)


class BoundingBox(BaseModel):
    """Normalized coordinates: [x0, y0, x1, y1] where (x0, y0) is top-left, (x1, y1) is bottom-right."""
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def to_list(self) -> list[float]:
        return [round(self.x0, 2), round(self.y0, 2), round(self.x1, 2), round(self.y1, 2)]

    @classmethod
    def from_rect(cls, rect: tuple[float, float, float, float] | list[float]) -> "BoundingBox":
        return cls(x0=rect[0], y0=rect[1], x1=rect[2], y1=rect[3])


class SourceProvenance(BaseModel):
    method: SourceMethod = SourceMethod.NATIVE_PDF
    engine: str = "pymupdf"
    version: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TextSpan(BaseModel):
    text: str
    bbox: BoundingBox
    font: Optional[str] = None
    size: Optional[float] = None
    flags: Optional[int] = None
    color: Optional[int] = None


class TableCell(BaseModel):
    id: str
    row: int
    column: int
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    text: str
    bbox: Optional[BoundingBox] = None
    confidence: float = 1.0


class AssetOccurrence(BaseModel):
    page: int
    bbox: BoundingBox


class Asset(BaseModel):
    id: str
    type: AssetType
    path: str
    mime_type: str
    sha256: str
    width: int
    height: int
    occurrences: list[AssetOccurrence] = Field(default_factory=list)
    source: SourceProvenance = Field(default_factory=SourceProvenance)


class DocumentElement(BaseModel):
    id: str
    type: ElementType
    page: int
    bbox: BoundingBox
    reading_order: int
    content: str
    confidence: float = 1.0
    provenance: SourceProvenance = Field(default_factory=SourceProvenance)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HeadingElement(DocumentElement):
    type: ElementType = ElementType.SECTION_HEADER
    level: int = 1
    section_id: Optional[str] = None


class TableElement(DocumentElement):
    type: ElementType = ElementType.TABLE
    rows: int
    columns: int
    cells: list[TableCell] = Field(default_factory=list)
    markdown: Optional[str] = None
    html: Optional[str] = None
    caption_id: Optional[str] = None


class FigureElement(DocumentElement):
    type: ElementType = ElementType.FIGURE
    asset_id: Optional[str] = None
    asset_path: Optional[str] = None
    title: Optional[str] = None
    caption_id: Optional[str] = None


class ChartElement(DocumentElement):
    type: ElementType = ElementType.CHART
    asset_id: Optional[str] = None
    asset_path: Optional[str] = None
    chart_type: Optional[str] = None
    title: Optional[str] = None
    caption_id: Optional[str] = None
    axes: Optional[dict[str, Any]] = None
    series: Optional[list[dict[str, Any]]] = None
    extracted_data_confidence: Optional[float] = None


class DiagramElement(DocumentElement):
    type: ElementType = ElementType.DIAGRAM
    asset_id: Optional[str] = None
    asset_path: Optional[str] = None
    caption_id: Optional[str] = None


class FormulaElement(DocumentElement):
    type: ElementType = ElementType.EQUATION
    latex: Optional[str] = None
    raw_text: Optional[str] = None
    is_inline: bool = False


class CaptionElement(DocumentElement):
    type: ElementType = ElementType.CAPTION
    target_element_id: Optional[str] = None


GenericElement = Union[
    TableElement,
    ChartElement,
    FigureElement,
    DiagramElement,
    FormulaElement,
    HeadingElement,
    CaptionElement,
    DocumentElement,
]


class Section(BaseModel):
    id: str
    title: str
    level: int = 1
    parent_id: Optional[str] = None
    element_ids: list[str] = Field(default_factory=list)


class Relationship(BaseModel):
    id: str
    type: RelationshipType
    source_id: str
    target_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PageQuality(BaseModel):
    text_confidence: float = 1.0
    layout_confidence: float = 1.0
    ocr_applied: bool = False
    warnings: list[str] = Field(default_factory=list)


class Page(BaseModel):
    page_number: int
    width: float
    height: float
    rotation: int = 0
    elements: list[GenericElement] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    quality: PageQuality = Field(default_factory=PageQuality)


class DocumentMetadata(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    creator: Optional[str] = None
    producer: Optional[str] = None
    creation_date: Optional[str] = None
    modification_date: Optional[str] = None
    page_count: int = 0
    file_size_bytes: int = 0
    sha256: str = ""


class DocumentQuality(BaseModel):
    overall_score: float = 1.0
    text_score: float = 1.0
    layout_score: float = 1.0
    table_score: float = 1.0
    warnings: list[str] = Field(default_factory=list)


class Document(BaseModel):
    id: str
    schema_version: str = "1.0"
    engine_version: str = "0.1.0"
    source_file: Optional[str] = None
    metadata: DocumentMetadata
    pages: list[Page] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    assets: dict[str, Asset] = Field(default_factory=dict)
    relationships: list[Relationship] = Field(default_factory=list)
    quality: DocumentQuality = Field(default_factory=DocumentQuality)
    warnings: list[str] = Field(default_factory=list)
