from __future__ import annotations

from pydantic import BaseModel, Field


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
