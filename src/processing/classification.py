from __future__ import annotations

import re

import pymupdf as fitz  # PyMuPDF

from trueparse.models.elements import (
    DocumentElement,
    FormulaElement,
    HeadingElement,
    SourceProvenance,
)
from trueparse.models.enums import ElementType, SourceMethod
from trueparse.models.text import RawTextBlock
from trueparse.processing.typography import FontProfile

_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$", re.DOTALL)
_NAMED_HEADING_RE = re.compile(
    r"^(chapter|section|part|appendix|annex)\s+([0-9ivxlcIVXLC]+)\b",
    re.IGNORECASE,
)
_CAPTION_RE = re.compile(
    r"^\s*(figure|fig\.?|table|chart|exhibit|scheme|listing)\s*[\d.]+\s*[:.—-]?",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*([•●▪‣⁃·*\-–—]|\(?[a-zA-Z0-9]{1,3}[.)])\s+")
_MATH_CHARS = set("∑∏∫√±×÷≠≤≥≈∞∂∇∈∉⊂⊃∪∩→←↔⇒⇔αβγδεθλμπσφψωΓΔΘΛΞΠΣΦΨΩ")
_MATH_FONT_HINTS = ("cmmi", "cmsy", "cmex", "mathjax", "msam", "msbm", "euclid", "symbol")


class TextClassifier:
    @classmethod
    def classify_and_build_elements(
        cls,
        raw_blocks: list[RawTextBlock],
        body_font_size: float,
        page_height: float,
        font_profile: FontProfile | None = None,
        detect_formulas: bool = True,
        start_index: int = 1,
    ) -> list[DocumentElement | HeadingElement]:
        """Classifies native text blocks into titles, headers, paragraphs, footers, etc.

        Args:
            raw_blocks: Blocks for a single page.
            body_font_size: Page-local body size, used only when no
                ``font_profile`` is supplied.
            page_height: Page height in points, for margin detection.
            font_profile: Document-wide typography. Strongly preferred.
            detect_formulas: Tag maths-heavy blocks as EQUATION elements.
        """
        profile = font_profile or FontProfile(body_size=body_font_size, ladder=[])
        elements: list[DocumentElement | HeadingElement] = []

        for idx, block in enumerate(raw_blocks):
            elem_id = f"elem_p{block.page_number:04d}_{idx + start_index:04d}"
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
