from __future__ import annotations
from typing import Optional
import pymupdf as fitz  # PyMuPDF
import statistics

from trueparse.core.enums import ElementType, SourceMethod
from trueparse.core.models import (
    BoundingBox,
    DocumentElement,
    HeadingElement,
    SourceProvenance,
    TextSpan,
)


class RawTextBlock:
    def __init__(
        self,
        bbox: BoundingBox,
        text: str,
        spans: list[TextSpan],
        avg_font_size: float,
        is_bold: bool,
        page_number: int,
    ):
        self.bbox = bbox
        self.text = text
        self.spans = spans
        self.avg_font_size = avg_font_size
        self.is_bold = is_bold
        self.page_number = page_number


class NativeExtractor:
    """Extracts native text blocks, spans, fonts, and geometry from PDF pages."""

    @classmethod
    def extract_page_text_blocks(
        cls,
        page: fitz.Page,
        page_number: int,
    ) -> tuple[list[RawTextBlock], float]:
        """
        Extracts structured raw text blocks from a page.
        Also returns the estimated body font size for the page.
        """
        page_dict = page.get_text("dict") or {}
        blocks_data = page_dict.get("blocks", [])

        raw_blocks: list[RawTextBlock] = []
        all_font_sizes: list[float] = []

        for block in blocks_data:
            # Block type 0 is text (type 1 is image)
            if block.get("type") != 0:
                continue

            lines = block.get("lines", [])
            if not lines:
                continue

            block_spans: list[TextSpan] = []
            block_lines_text: list[str] = []
            font_sizes: list[float] = []
            has_bold = False

            for line in lines:
                line_spans_text: list[str] = []
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    if not span_text.strip():
                        continue

                    s_bbox = BoundingBox.from_rect(span.get("bbox", (0, 0, 0, 0)))
                    s_font = span.get("font")
                    s_size = span.get("size", 10.0)
                    s_flags = span.get("flags", 0)
                    s_color = span.get("color")

                    # flags & 2 or 2**4 usually indicates bold in fitz
                    if (s_flags & 2 != 0) or (s_flags & 16 != 0) or ("bold" in (s_font or "").lower()):
                        has_bold = True

                    font_sizes.append(s_size)
                    all_font_sizes.append(s_size)

                    block_spans.append(
                        TextSpan(
                            text=span_text,
                            bbox=s_bbox,
                            font=s_font,
                            size=s_size,
                            flags=s_flags,
                            color=s_color,
                        )
                    )
                    line_spans_text.append(span_text)

                if line_spans_text:
                    block_lines_text.append(" ".join(line_spans_text))

            full_text = "\n".join(block_lines_text).strip()
            if not full_text:
                continue

            b_rect = block.get("bbox", (0, 0, 0, 0))
            bbox = BoundingBox.from_rect(b_rect)
            avg_size = statistics.mean(font_sizes) if font_sizes else 10.0

            raw_blocks.append(
                RawTextBlock(
                    bbox=bbox,
                    text=full_text,
                    spans=block_spans,
                    avg_font_size=avg_size,
                    is_bold=has_bold,
                    page_number=page_number,
                )
            )

        body_font_size = statistics.median(all_font_sizes) if all_font_sizes else 10.0
        return raw_blocks, body_font_size

    @classmethod
    def classify_and_build_elements(
        cls,
        raw_blocks: list[RawTextBlock],
        body_font_size: float,
        page_height: float,
        page_width: float,
    ) -> list[DocumentElement | HeadingElement]:
        """Classifies native text blocks into titles, headers, paragraphs, footers, etc."""
        elements: list[DocumentElement | HeadingElement] = []

        for idx, block in enumerate(raw_blocks):
            elem_id = f"elem_p{block.page_number:04d}_{idx + 1:04d}"
            bbox = block.bbox
            text = block.text.strip()

            # Detect Header / Footer / Page Number
            is_top_margin = bbox.y1 < page_height * 0.07
            is_bottom_margin = bbox.y0 > page_height * 0.93

            elem_type = ElementType.PARAGRAPH
            heading_level = 1

            if is_top_margin:
                elem_type = ElementType.HEADER
            elif is_bottom_margin:
                if text.isdigit() or len(text) < 10 and ("page" in text.lower() or text.startswith("-")):
                    elem_type = ElementType.PAGE_NUMBER
                else:
                    elem_type = ElementType.FOOTER
            else:
                # Heading detection heuristic:
                # If font size is noticeably larger than body font size or bold single line
                ratio = block.avg_font_size / max(1.0, body_font_size)
                is_short = len(text) < 200 and "\n" not in text

                if ratio >= 1.6:
                    elem_type = ElementType.TITLE
                    heading_level = 1
                elif ratio >= 1.25 or (ratio >= 1.05 and block.is_bold and is_short):
                    elem_type = ElementType.SECTION_HEADER
                    heading_level = 2 if ratio < 1.4 else 1
                elif text.lower().startswith(("figure ", "fig. ", "chart ", "table ")):
                    elem_type = ElementType.CAPTION
                elif text.startswith(("- ", "* ", "• ", "1. ", "2. ", "3. ")):
                    elem_type = ElementType.LIST

            provenance = SourceProvenance(
                method=SourceMethod.NATIVE_PDF,
                engine="pymupdf",
                version=fitz.__version__,
                confidence=0.99,
            )

            if elem_type in (ElementType.SECTION_HEADER, ElementType.TITLE):
                elements.append(
                    HeadingElement(
                        id=elem_id,
                        type=elem_type,
                        page=block.page_number,
                        bbox=bbox,
                        reading_order=idx + 1,
                        content=text,
                        confidence=0.98,
                        provenance=provenance,
                        level=heading_level,
                        metadata={
                            "avg_font_size": round(block.avg_font_size, 2),
                            "is_bold": block.is_bold,
                        },
                    )
                )
            else:
                elements.append(
                    DocumentElement(
                        id=elem_id,
                        type=elem_type,
                        page=block.page_number,
                        bbox=bbox,
                        reading_order=idx + 1,
                        content=text,
                        confidence=0.99,
                        provenance=provenance,
                        metadata={
                            "avg_font_size": round(block.avg_font_size, 2),
                            "is_bold": block.is_bold,
                        },
                    )
                )

        return elements
