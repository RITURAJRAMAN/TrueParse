from __future__ import annotations

from trueparse.models.elements import (
    TextSpan,
)
from trueparse.models.geometry import BoundingBox


class RawTextBlock:
    def __init__(
        self,
        bbox: BoundingBox,
        text: str,
        spans: list[TextSpan],
        avg_font_size: float,
        is_bold: bool,
        page_number: int,
        char_count: int = 0,
        line_count: int = 1,
        fonts: list[str] | None = None,
        ocr_confidence: float | None = None,
    ):
        self.bbox = bbox
        self.text = text
        self.spans = spans
        self.avg_font_size = avg_font_size
        self.is_bold = is_bold
        self.page_number = page_number
        self.char_count = char_count or len(text)
        self.line_count = line_count
        self.fonts = fonts or []
        #: Set only for blocks recovered by OCR; None for native text.
        self.ocr_confidence = ocr_confidence
