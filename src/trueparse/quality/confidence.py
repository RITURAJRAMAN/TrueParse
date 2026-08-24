from __future__ import annotations
import statistics
from trueparse.core.models import DocumentQuality, PageQuality, Page


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
    ) -> PageQuality:
        warnings: list[str] = []
        text_conf = 0.99 if has_native_text else (0.5 if likely_scan else 0.8)
        layout_conf = 0.95

        if likely_scan:
            warnings.append(f"Page {page_num} appears to be a scanned image; OCR may be required for full text extraction.")
        if text_elements_count == 0 and not likely_scan:
            warnings.append(f"Page {page_num} yielded no text elements.")

        return PageQuality(
            text_confidence=text_conf,
            layout_confidence=layout_conf,
            ocr_applied=False,
            warnings=warnings,
        )

    @classmethod
    def evaluate_document(cls, pages: list[Page]) -> DocumentQuality:
        if not pages:
            return DocumentQuality(
                overall_score=0.0,
                text_score=0.0,
                layout_score=0.0,
                table_score=0.0,
                warnings=["Document has no pages."],
            )

        text_scores = [p.quality.text_confidence for p in pages]
        layout_scores = [p.quality.layout_confidence for p in pages]

        all_warnings: list[str] = []
        for p in pages:
            all_warnings.extend(p.quality.warnings)

        avg_text = statistics.mean(text_scores) if text_scores else 1.0
        avg_layout = statistics.mean(layout_scores) if layout_scores else 1.0
        overall = (avg_text * 0.6) + (avg_layout * 0.4)

        return DocumentQuality(
            overall_score=round(overall, 3),
            text_score=round(avg_text, 3),
            layout_score=round(avg_layout, 3),
            table_score=0.95,
            warnings=all_warnings,
        )
