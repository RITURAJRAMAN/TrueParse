from trueparse.core.config import ParseOptions
from trueparse.core.enums import ElementType
from trueparse.core.models import BoundingBox, DocumentElement, Page, TableCell, TableElement
from trueparse.pipeline.runner import PDFParser
from trueparse.quality.confidence import QualityEngine


def _element(elem_id: str, x0, y0, x1, y1, elem_type=ElementType.PARAGRAPH) -> DocumentElement:
    return DocumentElement(
        id=elem_id, type=elem_type, page=1,
        bbox=BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1),
        reading_order=1, content="content",
    )


class TestPageQuality:
    def test_layout_confidence_drops_with_overlap(self):
        clean = [
            _element("a", 50, 100, 550, 200),
            _element("b", 50, 250, 550, 350),
        ]
        overlapping = [
            _element("a", 50, 100, 550, 200),
            _element("b", 50, 110, 550, 210),
        ]
        good = QualityEngine.evaluate_page(
            1, 2, 0, True, False, elements=clean, page_width=612, page_height=792
        )
        bad = QualityEngine.evaluate_page(
            1, 2, 0, True, False, elements=overlapping, page_width=612, page_height=792
        )
        assert bad.overlap_ratio > good.overlap_ratio
        assert bad.layout_confidence < good.layout_confidence

    def test_unknown_elements_reduce_layout_confidence(self):
        known = [_element("a", 50, 100, 550, 700)]
        unknown = [_element("a", 50, 100, 550, 700, ElementType.UNKNOWN)]
        good = QualityEngine.evaluate_page(
            1, 1, 0, True, False, elements=known, page_width=612, page_height=792
        )
        bad = QualityEngine.evaluate_page(
            1, 1, 0, True, False, elements=unknown, page_width=612, page_height=792
        )
        assert bad.unknown_ratio == 1.0
        assert bad.layout_confidence < good.layout_confidence

    def test_coverage_ratio_is_measured(self):
        quality = QualityEngine.evaluate_page(
            1, 1, 0, True, False,
            elements=[_element("a", 0, 0, 306, 396)],
            page_width=612, page_height=792,
        )
        assert 0.2 < quality.coverage_ratio < 0.3

    def test_scanned_page_without_ocr_warns(self):
        quality = QualityEngine.evaluate_page(
            1, 0, 0, has_native_text=False, likely_scan=True,
            elements=[], page_width=612, page_height=792,
        )
        assert quality.text_confidence < 0.5
        assert any("scanned" in w for w in quality.warnings)

    def test_ocr_confidence_feeds_text_confidence(self):
        low = QualityEngine.evaluate_page(
            1, 3, 0, False, True, elements=[], page_width=612, page_height=792,
            ocr_applied=True, ocr_confidence=0.4,
        )
        high = QualityEngine.evaluate_page(
            1, 3, 0, False, True, elements=[], page_width=612, page_height=792,
            ocr_applied=True, ocr_confidence=0.95,
        )
        assert high.text_confidence > low.text_confidence
        assert any("low" in w for w in low.warnings)
        assert not any("low" in w for w in high.warnings)


class TestDocumentQuality:
    def test_empty_document_scores_zero(self):
        quality = QualityEngine.evaluate_document([])
        assert quality.overall_score == 0.0
        assert quality.warnings

    def test_table_score_reflects_cell_density(self):
        def page_with(table_text: str) -> Page:
            table = TableElement(
                id="t", type=ElementType.TABLE, page=1,
                bbox=BoundingBox(x0=50, y0=100, x1=550, y1=200),
                reading_order=1, content="", rows=1, columns=2,
                cells=[
                    TableCell(id="c1", row=0, column=0, text=table_text),
                    TableCell(id="c2", row=0, column=1, text=table_text),
                ],
            )
            page = Page(page_number=1, width=612, height=792, elements=[table])
            page.quality = QualityEngine.evaluate_page(
                1, 0, 1, True, False, elements=[table], page_width=612, page_height=792
            )
            return page

        full = QualityEngine.evaluate_document([page_with("value")])
        empty = QualityEngine.evaluate_document([page_with("")])
        assert full.table_score > empty.table_score

    def test_no_tables_scores_one(self):
        page = Page(page_number=1, width=612, height=792, elements=[])
        assert QualityEngine.evaluate_document([page]).table_score == 1.0

    def test_score_varies_across_documents(self, tmp_path, sample_pdf_path, three_column_pdf_path):
        """The pre-0.1.2 score was built from constants and barely moved."""
        options = ParseOptions(output_path=str(tmp_path))
        parser = PDFParser(options=options)
        a = parser.parse(sample_pdf_path).quality
        b = parser.parse(three_column_pdf_path).quality
        assert (a.layout_score, a.table_score, a.coverage_score) != (
            b.layout_score, b.table_score, b.coverage_score
        )
        assert 0.0 < a.overall_score <= 1.0
        assert 0.0 < b.overall_score <= 1.0

    def test_layout_score_is_not_a_constant(self, tmp_path, sample_pdf_path):
        options = ParseOptions(output_path=str(tmp_path))
        doc = PDFParser(options=options).parse(sample_pdf_path)
        layout_scores = {p.quality.layout_confidence for p in doc.pages}
        assert len(layout_scores) > 1, "layout confidence should differ between pages"
