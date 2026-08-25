"""Concrete tuning behind each :class:`~trueparse.core.enums.ParsingProfile`.

Before 0.1.2 the ``profile`` option was accepted everywhere and read nowhere.
A profile now resolves to a real set of engine knobs that the pipeline consults
on every page.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from trueparse.core.enums import OCRMode, ParsingProfile


class ProfileSettings(BaseModel):
    """Resolved engine tuning for one parsing profile."""

    render_dpi: int = Field(description="DPI used for region crops and OCR rasterisation")
    table_strategies: list[str] = Field(
        description="Ordered PyMuPDF table strategies to attempt ('lines', 'text')"
    )
    detect_table_spans: bool = Field(description="Reconstruct row/column spans from the cell grid")
    merge_cross_page_tables: bool = Field(description="Join tables continuing across a page break")
    merge_paragraphs: bool = Field(description="Rejoin sentences split across columns/pages")
    ocr_floor: OCRMode = Field(
        description="Minimum OCR aggressiveness; AUTO honours per-page scan detection"
    )
    ocr_min_chars: int = Field(
        description="A page with fewer native characters than this is a candidate for OCR"
    )
    heading_font_clustering: bool = Field(
        description="Cluster font styles document-wide to build the heading ladder"
    )


_PROFILES: dict[ParsingProfile, ProfileSettings] = {
    ParsingProfile.FAST: ProfileSettings(
        render_dpi=96,
        table_strategies=["lines"],
        detect_table_spans=False,
        merge_cross_page_tables=False,
        merge_paragraphs=False,
        ocr_floor=OCRMode.NEVER,
        ocr_min_chars=0,
        heading_font_clustering=False,
    ),
    ParsingProfile.BALANCED: ProfileSettings(
        render_dpi=150,
        table_strategies=["lines"],
        detect_table_spans=True,
        merge_cross_page_tables=True,
        merge_paragraphs=True,
        ocr_floor=OCRMode.AUTO,
        ocr_min_chars=30,
        heading_font_clustering=True,
    ),
    ParsingProfile.ACCURATE: ProfileSettings(
        render_dpi=200,
        table_strategies=["lines", "text"],
        detect_table_spans=True,
        merge_cross_page_tables=True,
        merge_paragraphs=True,
        ocr_floor=OCRMode.AUTO,
        ocr_min_chars=80,
        heading_font_clustering=True,
    ),
    ParsingProfile.MAXIMUM_ACCURACY: ProfileSettings(
        render_dpi=300,
        table_strategies=["lines", "text"],
        detect_table_spans=True,
        merge_cross_page_tables=True,
        merge_paragraphs=True,
        ocr_floor=OCRMode.AUTO,
        ocr_min_chars=200,
        heading_font_clustering=True,
    ),
}


def resolve(profile: ParsingProfile) -> ProfileSettings:
    """Returns the engine tuning for ``profile``."""
    return _PROFILES.get(profile, _PROFILES[ParsingProfile.BALANCED]).model_copy(deep=True)
