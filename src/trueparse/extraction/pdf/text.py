from __future__ import annotations

import statistics

import pymupdf as fitz  # PyMuPDF

from trueparse.models.elements import (
    TextSpan,
)
from trueparse.models.geometry import BoundingBox
from trueparse.models.text import RawTextBlock


class NativeExtractor:
    @classmethod
    def extract_page_text_blocks(
        cls,
        page: fitz.Page,
        page_number: int,
    ) -> tuple[list[RawTextBlock], float]:
        page_dict = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES) or {}
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
            block_fonts: list[str] = []
            has_bold = False
            char_count = 0

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
                    char_count += len(span_text.strip())
                    if s_font:
                        block_fonts.append(s_font)

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
                    char_count=char_count,
                    line_count=len(block_lines_text),
                    fonts=sorted(set(block_fonts)),
                )
            )

        body_font_size = statistics.median(all_font_sizes) if all_font_sizes else 10.0
        return raw_blocks, body_font_size
