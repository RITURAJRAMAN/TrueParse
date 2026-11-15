from __future__ import annotations

from dataclasses import dataclass, field

from trueparse.models.text import RawTextBlock


@dataclass
class PageScan:
    """What the first pass recovered from one page.

    The pipeline reads every page before classifying any of them, because
    heading detection needs typography statistics for the whole document.
    """

    page_number: int
    raw_blocks: list[RawTextBlock] = field(default_factory=list)
    tables: list = field(default_factory=list)
    page_body_font: float = 10.0
    ocr_applied: bool = False
    ocr_confidence: float | None = None
    scan_failed: bool = False
    scan_error: str | None = None
    warnings: list[str] = field(default_factory=list)
