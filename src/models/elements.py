from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field

from trueparse.models.enums import ElementType, SourceMethod
from trueparse.models.geometry import BoundingBox


class SourceProvenance(BaseModel):
    method: SourceMethod = SourceMethod.NATIVE_PDF
    engine: str = "pymupdf"
    version: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TextSpan(BaseModel):
    text: str
    bbox: BoundingBox | None = None
    font: str | None = None
    size: float | None = None
    flags: int | None = None
    color: int | None = None


class TableCell(BaseModel):
    id: str
    row: int
    column: int
    page: int | None = None
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    text: str
    kind: Literal["text", "visual_marker"] = "text"
    bbox: BoundingBox | None = None
    confidence: float = 1.0


class DocumentElement(BaseModel):
    id: str
    type: ElementType
    page: int
    bbox: BoundingBox | None = None
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
