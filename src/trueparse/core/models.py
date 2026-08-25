from __future__ import annotations

from typing import Any, Union

from pydantic import BaseModel, Field

from trueparse.core.enums import (
    AssetType,
    ElementType,
    RelationshipType,
    SourceMethod,
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
    def from_rect(cls, rect: tuple[float, float, float, float] | list[float]) -> BoundingBox:
        return cls(x0=rect[0], y0=rect[1], x1=rect[2], y1=rect[3])


class SourceProvenance(BaseModel):
    method: SourceMethod = SourceMethod.NATIVE_PDF
    engine: str = "pymupdf"
    version: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TextSpan(BaseModel):
    text: str
    bbox: BoundingBox
    font: str | None = None
    size: float | None = None
    flags: int | None = None
    color: int | None = None


class TableCell(BaseModel):
    id: str
    row: int
    column: int
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    text: str
    bbox: BoundingBox | None = None
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
    section_id: str | None = None


class TableElement(DocumentElement):
    type: ElementType = ElementType.TABLE
    rows: int
    columns: int
    cells: list[TableCell] = Field(default_factory=list)
    markdown: str | None = None
    html: str | None = None
    caption_id: str | None = None


class FigureElement(DocumentElement):
    type: ElementType = ElementType.FIGURE
    asset_id: str | None = None
    asset_path: str | None = None
    title: str | None = None
    caption_id: str | None = None


class ChartElement(DocumentElement):
    type: ElementType = ElementType.CHART
    asset_id: str | None = None
    asset_path: str | None = None
    chart_type: str | None = None
    title: str | None = None
    caption_id: str | None = None
    axes: dict[str, Any] | None = None
    series: list[dict[str, Any]] | None = None
    extracted_data_confidence: float | None = None


class DiagramElement(DocumentElement):
    type: ElementType = ElementType.DIAGRAM
    asset_id: str | None = None
    asset_path: str | None = None
    caption_id: str | None = None


class FormulaElement(DocumentElement):
    type: ElementType = ElementType.EQUATION
    latex: str | None = None
    raw_text: str | None = None
    is_inline: bool = False


class CaptionElement(DocumentElement):
    type: ElementType = ElementType.CAPTION
    target_element_id: str | None = None


GenericElement = Union[  # noqa: UP007  (pydantic needs an explicit Union here)
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
    parent_id: str | None = None
    element_ids: list[str] = Field(default_factory=list)


class Relationship(BaseModel):
    id: str
    type: RelationshipType
    source_id: str
    target_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class PageQuality(BaseModel):
    text_confidence: float = 1.0
    layout_confidence: float = 1.0
    ocr_applied: bool = False
    ocr_confidence: float | None = Field(
        default=None,
        description="Mean OCR line confidence when OCR ran on this page",
    )
    coverage_ratio: float = Field(
        default=0.0,
        description="Fraction of page area covered by classified elements",
    )
    unknown_ratio: float = Field(
        default=0.0,
        description="Fraction of elements left as UNKNOWN after classification",
    )
    overlap_ratio: float = Field(
        default=0.0,
        description="Fraction of elements whose bbox materially overlaps another",
    )
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
    title: str | None = None
    author: str | None = None
    creator: str | None = None
    producer: str | None = None
    creation_date: str | None = None
    modification_date: str | None = None
    page_count: int = 0
    file_size_bytes: int = 0
    sha256: str = ""


class DocumentQuality(BaseModel):
    overall_score: float = 1.0
    text_score: float = 1.0
    layout_score: float = 1.0
    table_score: float = 1.0
    coverage_score: float = Field(
        default=0.0,
        description="Mean fraction of page area accounted for by classified elements",
    )
    ocr_pages: int = Field(default=0, description="Number of pages where OCR was applied")
    warnings: list[str] = Field(default_factory=list)


class Document(BaseModel):
    id: str
    schema_version: str = "1.0"
    engine_version: str = "0.1.0"
    source_file: str | None = None
    metadata: DocumentMetadata
    pages: list[Page] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    assets: dict[str, Asset] = Field(default_factory=dict)
    relationships: list[Relationship] = Field(default_factory=list)
    quality: DocumentQuality = Field(default_factory=DocumentQuality)
    warnings: list[str] = Field(default_factory=list)
