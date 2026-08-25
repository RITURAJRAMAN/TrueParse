"""Quality and confidence scoring.

Before 0.1.2 both ``layout_confidence`` and ``table_score`` were hard-coded
constants, which made ``overall_score`` vary only with a boolean and rendered
it useless as a signal. Every score here is now measured from the parse result.
"""
from __future__ import annotations

import statistics
from collections.abc import Sequence

from trueparse.core.enums import ElementType
from trueparse.core.models import (
    DocumentQuality,
    GenericElement,
    Page,
    PageQuality,
    TableElement,
)

#: Overlap beyond this fraction of the smaller element signals a layout failure.
_OVERLAP_THRESHOLD = 0.25

#: Typical share of a page covered by content once margins are removed.
_EXPECTED_COVERAGE = 0.45


class QualityEngine:
    """Calculates granular quality and confidence metrics for pages and documents."""

    @classmethod
    def evaluate_page(
        cls,
        page_num: int,
        text_elements_count: int,
        tables_count: int,
        has_native_text: bool,
        likely_scan: bool,
        elements: Sequence[GenericElement] | None = None,
        page_width: float = 0.0,
        page_height: float = 0.0,
        ocr_applied: bool = False,
        ocr_confidence: float | None = None,
    ) -> PageQuality:
        warnings: list[str] = []
        elements = list(elements or [])

        coverage = cls._coverage_ratio(elements, page_width, page_height)
        unknown = cls._unknown_ratio(elements)
        overlap = cls._overlap_ratio(elements)

        # Text confidence reflects where the characters actually came from.
        if ocr_applied and ocr_confidence is not None:
            text_conf = round(0.55 + 0.4 * ocr_confidence, 4)
        elif has_native_text:
            text_conf = 0.99
        elif likely_scan:
            text_conf = 0.35
        else:
            text_conf = 0.8

        # Layout confidence penalises unclassified and overlapping elements,
        # and pages where almost nothing was accounted for.
        coverage_component = min(1.0, coverage / _EXPECTED_COVERAGE) if page_width and page_height else 1.0
        layout_conf = round(
            max(
                0.0,
                (0.5 * coverage_component) + 0.5
                - (0.3 * unknown)
                - (0.25 * overlap),
            ),
            4,
        )
        layout_conf = min(1.0, layout_conf)

        if likely_scan and not ocr_applied:
            warnings.append(
                f"Page {page_num} appears to be a scanned image and OCR did not run; "
                "text extraction is incomplete. Install the OCR extra "
                "(pip install trueparse[ocr]) or set ocr=always."
            )
        if ocr_applied and ocr_confidence is not None and ocr_confidence < 0.6:
            warnings.append(
                f"Page {page_num} OCR confidence is low ({ocr_confidence:.2f}); "
                "verify extracted text before downstream use."
            )
        if text_elements_count == 0 and not likely_scan and not ocr_applied:
            warnings.append(f"Page {page_num} yielded no text elements.")
        if overlap > 0.3:
            warnings.append(
                f"Page {page_num} has heavily overlapping elements ({overlap:.0%}); "
                "reading order may be unreliable."
            )
        if unknown > 0.25:
            warnings.append(
                f"Page {page_num} left {unknown:.0%} of elements unclassified."
            )

        return PageQuality(
            text_confidence=text_conf,
            layout_confidence=layout_conf,
            ocr_applied=ocr_applied,
            ocr_confidence=ocr_confidence,
            coverage_ratio=round(coverage, 4),
            unknown_ratio=round(unknown, 4),
            overlap_ratio=round(overlap, 4),
            warnings=warnings,
        )

    @staticmethod
    def _coverage_ratio(
        elements: Sequence[GenericElement],
        page_width: float,
        page_height: float,
    ) -> float:
        """Fraction of the page area covered by classified elements.

        Overlapping boxes are counted once by summing areas and capping at the
        page area, which is close enough for a quality signal and far cheaper
        than a true polygon union.
        """
        page_area = page_width * page_height
        if page_area <= 0 or not elements:
            return 0.0
        covered = sum(e.bbox.area for e in elements)
        return min(1.0, covered / page_area)

    @staticmethod
    def _unknown_ratio(elements: Sequence[GenericElement]) -> float:
        if not elements:
            return 0.0
        unknown = sum(1 for e in elements if e.type == ElementType.UNKNOWN)
        return unknown / len(elements)

    @staticmethod
    def _overlap_ratio(elements: Sequence[GenericElement]) -> float:
        """Fraction of elements materially overlapping at least one other.

        Elements are swept in y-order so only vertically adjacent boxes are
        compared, keeping this linear-ish rather than quadratic on dense pages.
        """
        boxes = sorted(
            (e for e in elements if e.bbox.area > 0),
            key=lambda e: e.bbox.y0,
        )
        if len(boxes) < 2:
            return 0.0

        overlapping: set[int] = set()
        for i, a in enumerate(boxes):
            for j in range(i + 1, len(boxes)):
                b = boxes[j]
                if b.bbox.y0 >= a.bbox.y1:
                    break  # sorted by y0: nothing further can overlap a
                inter_w = min(a.bbox.x1, b.bbox.x1) - max(a.bbox.x0, b.bbox.x0)
                inter_h = min(a.bbox.y1, b.bbox.y1) - max(a.bbox.y0, b.bbox.y0)
                if inter_w <= 0 or inter_h <= 0:
                    continue
                inter = inter_w * inter_h
                if inter > _OVERLAP_THRESHOLD * min(a.bbox.area, b.bbox.area):
                    overlapping.add(i)
                    overlapping.add(j)

        return len(overlapping) / len(boxes)

    @classmethod
    def evaluate_document(cls, pages: list[Page]) -> DocumentQuality:
        if not pages:
            return DocumentQuality(
                overall_score=0.0,
                text_score=0.0,
                layout_score=0.0,
                table_score=0.0,
                coverage_score=0.0,
                warnings=["Document has no pages."],
            )

        text_scores = [p.quality.text_confidence for p in pages]
        layout_scores = [p.quality.layout_confidence for p in pages]
        coverage_scores = [p.quality.coverage_ratio for p in pages]

        all_warnings: list[str] = []
        for p in pages:
            all_warnings.extend(p.quality.warnings)

        avg_text = statistics.mean(text_scores) if text_scores else 1.0
        avg_layout = statistics.mean(layout_scores) if layout_scores else 1.0
        avg_coverage = statistics.mean(coverage_scores) if coverage_scores else 0.0
        table_score = cls._table_score(pages)
        ocr_pages = sum(1 for p in pages if p.quality.ocr_applied)

        overall = (avg_text * 0.5) + (avg_layout * 0.3) + (table_score * 0.2)

        return DocumentQuality(
            overall_score=round(overall, 3),
            text_score=round(avg_text, 3),
            layout_score=round(avg_layout, 3),
            table_score=round(table_score, 3),
            coverage_score=round(avg_coverage, 3),
            ocr_pages=ocr_pages,
            warnings=all_warnings,
        )

    @staticmethod
    def _table_score(pages: list[Page]) -> float:
        """Mean table confidence, weighted by how complete each grid is.

        A document with no tables scores 1.0: there is nothing to get wrong.
        """
        scores: list[float] = []
        for page in pages:
            for element in page.elements:
                if not isinstance(element, TableElement):
                    continue
                expected = max(1, element.rows * element.columns)
                # Spanned cells legitimately reduce the count, so completeness
                # is capped rather than penalised for a well-formed merge.
                filled = sum(c.row_span * c.col_span for c in element.cells)
                completeness = min(1.0, filled / expected)
                non_empty = sum(1 for c in element.cells if c.text.strip())
                density = non_empty / max(1, len(element.cells))
                scores.append(element.confidence * completeness * (0.5 + 0.5 * density))

        if not scores:
            return 1.0
        return statistics.mean(scores)
