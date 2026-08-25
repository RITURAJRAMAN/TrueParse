from __future__ import annotations

import re
import statistics
from collections import Counter
from collections.abc import Iterable

import pymupdf as fitz  # PyMuPDF
from pydantic import BaseModel, Field

from trueparse.core.enums import ElementType, SourceMethod
from trueparse.core.models import (
    BoundingBox,
    DocumentElement,
    FormulaElement,
    HeadingElement,
    SourceProvenance,
    TextSpan,
)

#: Leading heading numbers ("1.", "2.3", "4.1.2"); dot depth gives the level.
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$", re.DOTALL)

#: Appendix / chapter style prefixes that imply a top-level heading.
_NAMED_HEADING_RE = re.compile(
    r"^(chapter|section|part|appendix|annex)\s+([0-9ivxlcIVXLC]+)\b",
    re.IGNORECASE,
)

_CAPTION_RE = re.compile(
    r"^\s*(figure|fig\.?|table|chart|exhibit|scheme|listing)\s*[\d.]+\s*[:.—-]?",
    re.IGNORECASE,
)

#: Bullet glyphs and ordered-list markers seen at the start of a line.
_BULLET_RE = re.compile(r"^\s*([•●▪‣⁃·*\-–—]|\(?[a-zA-Z0-9]{1,3}[.)])\s+")

#: Characters that betray mathematical content rather than prose.
_MATH_CHARS = set("∑∏∫√±×÷≠≤≥≈∞∂∇∈∉⊂⊃∪∩→←↔⇒⇔αβγδεθλμπσφψωΓΔΘΛΞΠΣΦΨΩ")

#: Font-name fragments used by the standard TeX / Unicode math faces.
_MATH_FONT_HINTS = ("cmmi", "cmsy", "cmex", "mathjax", "msam", "msbm", "euclid", "symbol")


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


class FontProfile(BaseModel):
    """Document-wide typography statistics used to classify headings.

    Computing this across the whole document rather than per page fixes two
    common failure modes: a page consisting entirely of a heading (which has no
    body text to compare against) and a document whose body size drifts between
    sections.
    """

    body_size: float = Field(description="Character-weighted modal body font size")
    #: Distinct heading styles, largest first. Index + 1 is the heading level.
    ladder: list[float] = Field(default_factory=list)
    body_is_bold: bool = Field(
        default=False,
        description="True when body text is predominantly bold, disabling the bold heuristic",
    )

    def level_for(self, size: float, is_bold: bool) -> int | None:
        """Maps a font size onto a heading level, or None if it is body text."""
        for idx, rung in enumerate(self.ladder):
            # Sizes within a quarter point are the same style in practice.
            if size >= rung - 0.25:
                return idx + 1
        if is_bold and not self.body_is_bold and size >= self.body_size - 0.25:
            # Bold at body size is the weakest signal; park it below the ladder.
            return max(1, len(self.ladder)) + 1
        return None


class NativeExtractor:
    """Extracts native text blocks, spans, fonts, and geometry from PDF pages."""

    @classmethod
    def extract_page_text_blocks(
        cls,
        page: fitz.Page,
        page_number: int,
    ) -> tuple[list[RawTextBlock], float]:
        """Extracts structured raw text blocks from a page.

        Returns:
            The blocks, plus the page-local median font size. Prefer
            :meth:`build_font_profile` over that second value for
            classification; it is retained for backwards compatibility.
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

    @classmethod
    def build_font_profile(
        cls,
        blocks: Iterable[RawTextBlock],
        max_levels: int = 4,
    ) -> FontProfile:
        """Derives the body size and heading ladder from every block in a document.

        Body size is the *character-weighted* mode: a document with 40 short
        headings and 6 dense paragraphs must still resolve the paragraph size
        as body, which an unweighted median gets wrong.
        """
        blocks = list(blocks)
        if not blocks:
            return FontProfile(body_size=10.0, ladder=[])

        weighted: Counter[float] = Counter()
        bold_chars = 0
        total_chars = 0
        for block in blocks:
            size = round(block.avg_font_size, 1)
            weighted[size] += max(1, block.char_count)
            total_chars += max(1, block.char_count)
            if block.is_bold:
                bold_chars += max(1, block.char_count)

        body_size = weighted.most_common(1)[0][0]
        body_is_bold = total_chars > 0 and (bold_chars / total_chars) > 0.6

        # Heading sizes: larger than body, and not a big share of the text.
        candidates = sorted(
            (
                size
                for size, chars in weighted.items()
                if size >= body_size + 0.5 and chars / total_chars < 0.35
            ),
            reverse=True,
        )

        # Collapse sizes within half a point; they are the same visual style.
        ladder: list[float] = []
        for size in candidates:
            if not ladder or (ladder[-1] - size) > 0.5:
                ladder.append(size)
            if len(ladder) >= max_levels:
                break

        return FontProfile(body_size=body_size, ladder=ladder, body_is_bold=body_is_bold)

    @classmethod
    def classify_and_build_elements(
        cls,
        raw_blocks: list[RawTextBlock],
        body_font_size: float,
        page_height: float,
        page_width: float,
        font_profile: FontProfile | None = None,
        detect_formulas: bool = True,
    ) -> list[DocumentElement | HeadingElement]:
        """Classifies native text blocks into titles, headers, paragraphs, footers, etc.

        Args:
            raw_blocks: Blocks for a single page.
            body_font_size: Page-local body size, used only when no
                ``font_profile`` is supplied.
            page_height: Page height in points, for margin detection.
            page_width: Page width in points.
            font_profile: Document-wide typography. Strongly preferred.
            detect_formulas: Tag maths-heavy blocks as EQUATION elements.
        """
        profile = font_profile or FontProfile(body_size=body_font_size, ladder=[])
        elements: list[DocumentElement | HeadingElement] = []

        for idx, block in enumerate(raw_blocks):
            elem_id = f"elem_p{block.page_number:04d}_{idx + 1:04d}"
            bbox = block.bbox
            text = block.text.strip()

            elem_type, heading_level = cls._classify(
                block=block,
                text=text,
                profile=profile,
                page_height=page_height,
                detect_formulas=detect_formulas,
            )

            provenance = SourceProvenance(
                method=SourceMethod.NATIVE_PDF,
                engine="pymupdf",
                version=fitz.__version__,
                confidence=0.99,
            )
            metadata = {
                "avg_font_size": round(block.avg_font_size, 2),
                "is_bold": block.is_bold,
                "body_font_size": round(profile.body_size, 2),
            }

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
                        metadata=metadata,
                    )
                )
            elif elem_type == ElementType.EQUATION:
                elements.append(
                    FormulaElement(
                        id=elem_id,
                        type=ElementType.EQUATION,
                        page=block.page_number,
                        bbox=bbox,
                        reading_order=idx + 1,
                        content=text,
                        confidence=0.75,
                        provenance=SourceProvenance(
                            method=SourceMethod.HEURISTIC,
                            engine="trueparse_math_heuristic",
                            version=fitz.__version__,
                            confidence=0.75,
                        ),
                        raw_text=text,
                        is_inline=block.line_count == 1 and len(text) < 80,
                        metadata=metadata,
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
                        metadata=metadata,
                    )
                )

        return elements

    @classmethod
    def _classify(
        cls,
        block: RawTextBlock,
        text: str,
        profile: FontProfile,
        page_height: float,
        detect_formulas: bool,
    ) -> tuple[ElementType, int]:
        """Returns the element type and, for headings, the heading level."""
        bbox = block.bbox

        # Margins win outright: big text at the top is a running head.
        if bbox.y1 < page_height * 0.07:
            return ElementType.HEADER, 1
        if bbox.y0 > page_height * 0.93:
            if text.isdigit() or (len(text) < 10 and ("page" in text.lower() or text.startswith("-"))):
                return ElementType.PAGE_NUMBER, 1
            return ElementType.FOOTER, 1

        # Before headings: "Table 3.1" matches both patterns.
        if _CAPTION_RE.match(text):
            return ElementType.CAPTION, 1

        if detect_formulas and cls._looks_like_formula(block, text):
            return ElementType.EQUATION, 1

        is_short = len(text) < 200 and block.line_count <= 2

        # Numbering, before bullets: "1. Introduction" matches both patterns.
        numbered = _NUMBERED_HEADING_RE.match(text)
        if numbered and is_short and cls._numbering_implies_heading(numbered):
            depth = numbered.group(1).count(".") + 1
            return ElementType.SECTION_HEADER, min(6, depth)

        if _BULLET_RE.match(text):
            return ElementType.LIST, 1

        if is_short and _NAMED_HEADING_RE.match(text):
            return ElementType.SECTION_HEADER, 1

        # Signal 2: the document-wide font ladder.
        level = profile.level_for(round(block.avg_font_size, 1), block.is_bold)
        if level is not None and is_short:
            # The largest style is the title only if it towers over body text.
            if level == 1 and block.avg_font_size >= profile.body_size * 1.5:
                return ElementType.TITLE, 1
            return ElementType.SECTION_HEADER, min(6, level)

        return ElementType.PARAGRAPH, 1

    @staticmethod
    def _numbering_implies_heading(match: re.Match[str]) -> bool:
        """Disambiguates "1. Introduction" (heading) from "1. Buy milk." (list).

        Multi-level numbering ("1.2", "3.1.4") is essentially always a heading.
        Single-level numbering is ambiguous, so it additionally has to look like
        a title: short, and without the terminal punctuation that marks prose.
        """
        depth = match.group(1).count(".") + 1
        if depth > 1:
            return True
        title = match.group(2).strip()
        return len(title) < 80 and not title.endswith((".", "!", "?", ";", ","))

    @staticmethod
    def _looks_like_formula(block: RawTextBlock, text: str) -> bool:
        """Heuristic detection of displayed mathematics.

        Requires either a maths-specific font face or a high density of
        mathematical operators, plus low prose density, so that ordinary
        sentences containing an arrow or a multiplication sign do not qualify.
        """
        if len(text) > 400 or not text:
            return False

        fonts_lower = " ".join(block.fonts).lower()
        has_math_font = any(hint in fonts_lower for hint in _MATH_FONT_HINTS)

        math_hits = sum(1 for ch in text if ch in _MATH_CHARS)
        stripped = text.replace(" ", "")
        if not stripped:
            return False
        math_density = math_hits / len(stripped)

        letters = sum(1 for ch in text if ch.isalpha())
        prose_density = letters / len(stripped)

        if has_math_font and math_hits >= 1:
            return True
        # Operator-heavy and not sentence-like.
        return math_density >= 0.08 and prose_density < 0.55 and block.line_count <= 3
