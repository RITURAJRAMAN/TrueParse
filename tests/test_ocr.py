import pytest

from trueparse.core.config import ParseOptions
from trueparse.core.enums import OCRMode, ParsingProfile, SourceMethod
from trueparse.core.models import BoundingBox
from trueparse.ocr.engine import OCREngine, OCRLine, OCRPageResult, ocr_available
from trueparse.pipeline.runner import PDFParser


def _backend_loads() -> bool:
    """True only if the backend imports AND its models initialise.

    ocr_available() is a cheap spec probe, so it stays True on a Linux box
    missing libGL even though OpenCV cannot actually load.
    """
    if not ocr_available():
        return False
    from trueparse.ocr.engine import _load_engine

    engine, _ = _load_engine()
    return engine is not None


ocr_installed = pytest.mark.skipif(
    not _backend_loads(), reason="OCR backend is not installed or cannot load"
)


class TestGracefulDegradation:
    def test_availability_probe_never_raises(self):
        assert isinstance(ocr_available(), bool)
        assert isinstance(OCREngine.is_available(), bool)

    def test_recognize_returns_empty_when_unavailable(self):
        if ocr_available():
            pytest.skip("backend installed; degradation path not exercised")
        result = OCREngine.recognize(b"not-an-image")
        assert result.lines == []
        assert result.engine == "unavailable"

    def test_scanned_pdf_parses_without_the_extra(self, tmp_path, scanned_pdf_path):
        """A scanned PDF must still produce a document, OCR or not."""
        options = ParseOptions(output_path=str(tmp_path), ocr=OCRMode.AUTO)
        doc = PDFParser(options=options).parse(scanned_pdf_path)
        assert len(doc.pages) == 1
        assert (tmp_path / doc.id / "output" / "document.json").exists()

    def test_ocr_never_skips_even_on_a_scan(self, tmp_path, scanned_pdf_path):
        options = ParseOptions(output_path=str(tmp_path), ocr=OCRMode.NEVER)
        doc = PDFParser(options=options).parse(scanned_pdf_path)
        assert doc.quality.ocr_pages == 0
        assert not doc.pages[0].quality.ocr_applied

    def test_fast_profile_disables_ocr(self):
        from trueparse.core.profiles import resolve
        assert resolve(ParsingProfile.FAST).ocr_floor == OCRMode.NEVER


class TestBlockGrouping:
    def _line(self, text: str, y0: float, height: float = 12.0) -> OCRLine:
        return OCRLine(
            text=text,
            bbox=BoundingBox(x0=50.0, y0=y0, x1=300.0, y1=y0 + height),
            confidence=0.9,
        )

    def test_adjacent_lines_group_into_one_block(self):
        result = OCRPageResult(lines=[
            self._line("first line", 100.0),
            self._line("second line", 114.0),
        ])
        blocks = OCREngine.group_into_blocks(result)
        assert len(blocks) == 1
        assert blocks[0][0] == "first line\nsecond line"

    def test_large_vertical_gap_starts_a_new_block(self):
        result = OCRPageResult(lines=[
            self._line("paragraph one", 100.0),
            self._line("paragraph two", 300.0),
        ])
        blocks = OCREngine.group_into_blocks(result)
        assert len(blocks) == 2

    def test_block_bbox_is_the_union_of_its_lines(self):
        result = OCRPageResult(lines=[
            self._line("a", 100.0),
            self._line("b", 114.0),
        ])
        (_, bbox, confidence) = OCREngine.group_into_blocks(result)[0]
        assert bbox.y0 == 100.0
        assert bbox.y1 == 126.0
        assert confidence == pytest.approx(0.9)

    def test_empty_result_yields_no_blocks(self):
        assert OCREngine.group_into_blocks(OCRPageResult()) == []


@ocr_installed
class TestWithBackend:
    def test_scanned_page_recovers_text(self, tmp_path, scanned_pdf_path):
        options = ParseOptions(
            output_path=str(tmp_path), ocr=OCRMode.ALWAYS, profile=ParsingProfile.ACCURATE
        )
        doc = PDFParser(options=options).parse(scanned_pdf_path)

        assert doc.quality.ocr_pages == 1
        assert doc.pages[0].quality.ocr_applied
        assert doc.pages[0].quality.ocr_confidence is not None

        text = " ".join(e.content for e in doc.pages[0].elements).upper()
        assert "SCANNED" in text or "INVOICE" in text

    def test_ocr_elements_carry_ocr_provenance(self, tmp_path, scanned_pdf_path):
        options = ParseOptions(output_path=str(tmp_path), ocr=OCRMode.ALWAYS)
        doc = PDFParser(options=options).parse(scanned_pdf_path)
        methods = {e.provenance.method for e in doc.pages[0].elements}
        assert SourceMethod.OCR_MODEL in methods


class TestAutoModeSelectivity:
    def test_sparse_but_native_page_is_not_ocred(self, tmp_path, sample_pdf_path):
        """A short page with real native text must not be re-OCR'd."""
        options = ParseOptions(output_path=str(tmp_path), ocr=OCRMode.AUTO)
        doc = PDFParser(options=options).parse(sample_pdf_path)
        assert doc.quality.ocr_pages == 0
        assert all(not p.quality.ocr_applied for p in doc.pages)

    def test_scan_is_still_detected_under_auto(self, tmp_path, scanned_pdf_path):
        options = ParseOptions(output_path=str(tmp_path), ocr=OCRMode.AUTO)
        doc = PDFParser(options=options).parse(scanned_pdf_path)
        expected = ocr_available()
        assert doc.pages[0].quality.ocr_applied is expected
