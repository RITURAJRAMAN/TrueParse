"""Known-answer accuracy checks and injected stage failures for pipeline upgrades."""

from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from trueparse import ParseError, parse
from trueparse.chunking.chunker import DocumentChunker
from trueparse.chunking.tokenization import encoding_path, token_counter
from trueparse.config.options import ParseOptions
from trueparse.exporters.tables import TableRenderer
from trueparse.extraction.ocr.engine import OCREngine, OCRLine
from trueparse.extraction.ocr.recovery import missing_lines
from trueparse.extraction.pdf.inspector import PDFInspector
from trueparse.extraction.pdf.renderer import PDFRenderer
from trueparse.extraction.pdf.text import NativeExtractor
from trueparse.extraction.tables import NativeTableExtractor
from trueparse.models.document import Document, Page
from trueparse.models.elements import DocumentElement, TableCell, TableElement
from trueparse.models.enums import ElementType
from trueparse.models.geometry import BoundingBox
from trueparse.models.text import RawTextBlock
from trueparse.pipeline.runner import PDFParser
from trueparse.pipeline.stages.layout import build_pages
from trueparse.pipeline.stages.scan import scan_pages
from trueparse.processing.classification import TextClassifier
from trueparse.processing.graph import DocumentGraphBuilder
from trueparse.processing.hierarchy import HierarchyEngine
from trueparse.processing.reading_order import ReadingOrderEngine
from trueparse.processing.tables import TableMerger
from trueparse.processing.text_merge import merge_paragraphs
from trueparse.processing.typography import TypographyAnalyzer

TRUTH = json.loads((Path(__file__).parent / "fixtures" / "pipeline_truth.json").read_text(encoding="utf-8"))


def _ocr_ready():
    from trueparse.extraction.ocr.engine import _load_engine

    return _load_engine()[0] is not None


def _paragraph(text, page=1, element_id="paragraph"):
    return DocumentElement(
        id=element_id,
        type=ElementType.PARAGRAPH,
        page=page,
        reading_order=1,
        content=text,
        bbox=BoundingBox(x0=50, y0=100, x1=550, y1=140),
    )


def _table(page, *, top=50, bottom=200, caption="", hint=False, columns=("Metric", "Value")):
    return TableElement(
        id=f"table{page}",
        page=page,
        reading_order=1,
        bbox=BoundingBox(x0=50, y0=top, x1=550, y1=bottom),
        rows=3,
        columns=2,
        content="Metric\tValue\nRevenue\t100\nCosts\t40",
        cells=[
            TableCell(
                id=f"p{page}r{row}c{column}",
                row=row,
                column=column,
                page=page,
                text=columns[column] if row == 0 else f"value{page}{row}{column}",
                is_header=row == 0,
                bbox=BoundingBox(
                    x0=50 + column * 250, y0=top + row * 25, x1=300 + column * 250, y1=top + row * 25 + 25
                ),
            )
            for row in range(3)
            for column in range(2)
        ],
        metadata={"page_height": 800, "caption_text": caption, "continuation_hint": hint},
    )


def test_known_invoice_grid_and_text_are_exact(sample_pdf_path):
    result = parse(sample_pdf_path, formats=["json", "text"], ocr="never")
    expected = TRUTH["invoice"]
    table = next(element for element in result.document["nodes"].values() if element["type"] == "table")
    assert (table["rows"], table["columns"]) == (expected["rows"], expected["columns"])
    actual = [[""] * table["columns"] for _ in range(table["rows"])]
    for cell in table["cells"]:
        actual[cell["row"]][cell["column"]] = cell["text"]
    assert actual == expected["cells"]
    assert all(text in result.text for text in expected["required_text"])


def test_loading_failure_in_each_pass_keeps_later_pages(sample_pdf_path):
    parser = PDFParser(ParseOptions(ocr="never", output_config={"save_to_disk": False}))
    inspection = PDFInspector.inspect(sample_pdf_path)
    parser._page_warnings = {}
    parser.options.extract_charts = False

    class BrokenPage:
        def __init__(self, document):
            self.document = document

        def __getitem__(self, index):
            if index == 1:
                raise RuntimeError("broken page object")
            return self.document[index]

    with fitz.open(sample_pdf_path) as document:
        scans = scan_pages(parser, BrokenPage(document), inspection, 3, time.monotonic(), None)
        assert len(scans) == 3 and scans[1].scan_failed
        font = TypographyAnalyzer.build_font_profile([block for scan in scans for block in scan.raw_blocks])
        pages, _ = build_pages(
            parser,
            BrokenPage(document),
            inspection.document_id,
            inspection,
            scans,
            font,
            {},
            {},
            False,
            None,
            time.monotonic(),
        )
    assert len(pages) == 3
    assert any("Conclusion" in element.content for element in pages[2].elements)


def test_table_failure_does_not_prevent_native_extraction(sample_pdf_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("table detector unavailable")

    monkeypatch.setattr(NativeTableExtractor, "extract_page_tables", fail)
    result = parse(sample_pdf_path, formats=["text"], ocr="never")
    assert "Executive Summary" in result.text and "Conclusion" in result.text
    assert any("table detector unavailable" in warning for warning in result.warnings)
    assert result.metrics["stage_calls"]["native_text"] == 3


def test_terminal_all_page_failure_is_not_reported_as_success(sample_pdf_path, monkeypatch):
    monkeypatch.setattr(
        NativeExtractor, "extract_page_text_blocks", Mock(side_effect=RuntimeError("all text unavailable"))
    )
    with pytest.raises(ParseError) as error:
        parse(sample_pdf_path, formats=["text"], max_pages=1, ocr="never")
    assert error.value.code.value == "PDF_PARSE_ERROR"
    assert error.value.details["warnings"]


def test_single_block_failure_preserves_other_blocks(sample_pdf_path, monkeypatch):
    original = TextClassifier.classify_and_build_elements

    def fail(*args, **kwargs):
        if "test paragraph" in kwargs["raw_blocks"][0].text:
            raise RuntimeError("one bad block")
        return original(*args, **kwargs)

    monkeypatch.setattr(TextClassifier, "classify_and_build_elements", fail)
    result = parse(sample_pdf_path, formats=["text"], ocr="never")
    assert "Executive Summary" in result.text
    assert "Conclusion" in result.text
    assert any("one bad block" in warning for warning in result.warnings)


def test_ordering_failure_uses_recovered_extraction_order(sample_pdf_path, monkeypatch):
    monkeypatch.setattr(
        ReadingOrderEngine, "order_page_elements", Mock(side_effect=RuntimeError("bad layout"))
    )
    result = parse(sample_pdf_path, ocr="never")
    assert "Executive Summary" in result.markdown
    assert any("recovered extraction order" in warning for warning in result.warnings)


def test_one_bad_table_grid_does_not_hide_other_tables(monkeypatch):
    page = Mock()
    page.get_drawings.return_value = [{"rect": fitz.Rect(0, 0, 10, 10)}]
    page.find_tables.return_value.tables = [Mock(bbox=(0, 0, 100, 100)), Mock(bbox=(200, 0, 300, 100))]

    def reconstruct(**kwargs):
        if kwargs["table_idx"] == 1:
            raise RuntimeError("invalid grid")
        return _table(1)

    monkeypatch.setattr(NativeTableExtractor, "_build_element", reconstruct)
    warnings = []
    tables = NativeTableExtractor.extract_page_tables(page, 1, warnings=warnings)
    assert len(tables) == 1
    assert any("invalid grid" in warning for warning in warnings)


def test_plain_text_skips_unneeded_renderers_and_graph(sample_pdf_path, monkeypatch):
    for target, name in (
        (TableRenderer, "_to_html"),
        (TableRenderer, "_to_markdown"),
        (HierarchyEngine, "build_sections_and_captions"),
        (DocumentGraphBuilder, "build_relationships"),
    ):
        monkeypatch.setattr(target, name, Mock(side_effect=AssertionError("unrequested work")))
    result = parse(sample_pdf_path, formats=["text"], ocr="never")
    assert "Revenue" in result.text
    assert "table_rendering" not in result.metrics["stage_calls"]
    assert "hierarchy" not in result.metrics["stage_calls"]


def test_stage_metrics_are_finite_and_reset_between_calls(sample_pdf_path):
    parser = PDFParser(ParseOptions(ocr="never", output_config={"formats": ["text"], "save_to_disk": False}))
    for _ in range(2):
        result = parser.parse_to_response(sample_pdf_path)
        assert result.metrics["stage_calls"]["inspection"] == 1
        assert result.metrics["stage_calls"]["native_text"] == 3
        assert all(0 <= value < 60000 for value in result.metrics["stage_timings_ms"].values())
        assert "export_html" not in result.metrics["stage_timings_ms"]


def test_vector_icon_arithmetic_matches_rectangle_intersection():
    import random

    randomizer = random.Random(7)
    cell = BoundingBox(x0=50, y0=50, x1=250, y1=100)
    for _ in range(100):
        rect = fitz.Rect(
            randomizer.uniform(0, 250),
            randomizer.uniform(0, 100),
            randomizer.uniform(250, 500),
            randomizer.uniform(100, 200),
        )
        expected = (
            "[TrueParse vector mark]"
            if (rect & fitz.Rect(*cell.to_list())).get_area() > 0.3 * rect.get_area()
            and 3 < rect.width < cell.width * 0.85
            else ""
        )
        assert NativeTableExtractor._vector_glyph_placeholder(cell, [{"rect": rect}]) == expected
    assert (
        NativeTableExtractor._vector_glyph_placeholder(cell, [{"rect": fitz.Rect(60, 60, 70, 70)}])
        == "[TrueParse vector mark]"
    )


def test_repeated_headers_without_continuation_evidence_stay_separate():
    pages = [[_table(1)], [_table(2)]]
    TableMerger.merge_cross_page_tables(pages)
    assert [len(page) for page in pages] == [1, 1]


def test_page_boundaries_support_headerless_continuation():
    first, second = _table(1, top=650, bottom=790), _table(2, top=30, bottom=180)
    for cell in second.cells:
        cell.is_header = False
    pages = [[first], [second]]
    TableMerger.merge_cross_page_tables(pages)
    assert pages[1] == [] and first.rows == 6


@pytest.mark.parametrize(
    "reason",
    ["different_caption", "new_section", "closing_prose", "intro_prose", "page_gap", "changed_column_width"],
)
def test_unrelated_aligned_tables_are_not_consolidated(reason):
    first, second = _table(1, top=650, bottom=790), _table(2, top=30, bottom=180, hint=True)
    before, after = [], []
    if reason == "different_caption":
        first.metadata["caption_text"], second.metadata["caption_text"] = "Table 1 Sales", "Table 2 Costs"
    elif reason == "new_section":
        before = [
            DocumentElement(
                id="heading", type=ElementType.SECTION_HEADER, page=2, reading_order=1, content="New subject"
            )
        ]
    elif reason == "closing_prose":
        after = [_paragraph("This paragraph closes the previous table and explains a separate subject.")]
    elif reason == "intro_prose":
        before = [
            _paragraph(
                "An independent new discussion introduces another financial schedule with additional commentary.",
                page=2,
            )
        ]
    elif reason == "page_gap":
        second.page = 3
    else:
        for cell in second.cells:
            cell.bbox.x1 += 100
    pages = [[first, *after], [*before, second]]
    TableMerger.merge_cross_page_tables(pages)
    assert first in pages[0] and second in pages[1]


def test_ordered_header_rows_are_removed_once_in_continuation():
    first, second = _table(1, top=650, bottom=790), _table(2, hint=True)
    for table in (first, second):
        for cell in table.cells:
            cell.is_header = cell.row < 2
            if cell.row == 1:
                cell.text = "Subheader" + str(cell.column)
    TableMerger.merge_cross_page_tables([[first], [second]])
    assert first.rows == 4
    assert len([cell for cell in first.cells if cell.text == "Subheader0"]) == 1


def test_chunk_ids_are_unique_across_documents_and_deterministic():
    def document(document_id):
        return Document(
            id=document_id,
            pages=[Page(page_number=1, width=600, height=800, elements=[_paragraph("word " * 100)])],
        )

    first = DocumentChunker.chunk(document("doc_A"), max_tokens=32)
    second = DocumentChunker.chunk(document("doc_B"), max_tokens=32)
    assert not {chunk.id for chunk in first} & {chunk.id for chunk in second}
    assert [chunk.id for chunk in first] == [
        chunk.id for chunk in DocumentChunker.chunk(document("doc_A"), max_tokens=32)
    ]


def test_character_spans_reconstruct_each_prose_slice():
    text = "A paragraph with real character offsets. " * 30
    element = _paragraph(text)
    document = Document(id="offsets", pages=[Page(page_number=1, width=600, height=800, elements=[element])])
    covered = set()
    for chunk in DocumentChunker.chunk(document, max_tokens=32, overlap_tokens=8):
        assert chunk.token_estimate <= 32
        for span in chunk.source_spans:
            assert element.content[span.start : span.end] in chunk.text
            covered.update(range(span.start, span.end))
    assert all(index in covered for index, char in enumerate(text) if not char.isspace())


def test_healed_paragraph_retains_both_pages_and_original_elements():
    first = _paragraph("Payment terms require " * 40, element_id="first")
    second = _paragraph("continuation of the terms until the end.", page=2, element_id="second")
    pages = merge_paragraphs([[first], [second]])
    document = Document(
        id="healed",
        pages=[
            Page(page_number=i + 1, width=600, height=800, elements=elements)
            for i, elements in enumerate(pages)
        ],
    )
    chunks = DocumentChunker.chunk(document, max_tokens=32, overlap_tokens=0)
    assert any(chunk.page_start == chunk.page_end == 1 for chunk in chunks)
    assert any(chunk.page_end == 2 for chunk in chunks)
    assert any(
        span.source_element_id == "second" and span.page == 2
        for chunk in chunks
        for span in chunk.source_spans
    )


def test_atomic_row_overflow_is_explicit():
    table = TableElement(
        id="huge",
        page=1,
        reading_order=1,
        rows=2,
        columns=1,
        content="table",
        markdown="| Heading |\n| --- |\n| " + "word " * 200 + " |",
    )
    doc = Document(id="large", pages=[Page(page_number=1, width=600, height=800, elements=[table])])
    chunks = DocumentChunker.chunk(doc, max_tokens=32)
    assert len(chunks) == 1 and chunks[0].budget_exceeded
    assert chunks[0].source_spans[0].row_start == 1


def test_repeated_table_headers_and_precise_page_spans():
    first, second = _table(1, top=650, bottom=790), _table(2, hint=True)
    TableMerger.merge_cross_page_tables([[first], [second]])
    TableRenderer.ensure_representations(first, markdown=True, html=False)
    doc = Document(id="tables", pages=[Page(page_number=1, width=600, height=800, elements=[first])])
    chunks = DocumentChunker.chunk(doc, max_tokens=18)
    assert chunks[0].page_end == 1
    assert chunks[-1].page_end == 2
    assert all(chunk.text.startswith("| Metric | Value |") for chunk in chunks)
    assert any(span.page == 2 and span.row_start >= 3 for chunk in chunks for span in chunk.source_spans)


def test_missing_tokenizer_data_never_triggers_a_download(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUEPARSE_TOKENIZER_FILE", str(tmp_path / "not-provisioned"))
    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=AssertionError("network must not run")))
    with pytest.raises(ParseError, match="Provision cl100k_base"):
        token_counter("cl100k_base")


def test_exact_local_counter_matches_tiktoken_and_enforces_unicode_budget(monkeypatch):
    tiktoken = pytest.importorskip("tiktoken")
    if not encoding_path().is_file():
        pytest.skip("Provision tokenizer data during setup to test exact counting")
    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=AssertionError("offline parse")))
    exact = token_counter("cl100k_base")
    reference = tiktoken.get_encoding("cl100k_base")
    text = "客户发票 café 👩🏽‍💻 payment_total=1234.50; " * 30
    assert exact(text) == len(reference.encode(text))
    doc = Document(
        id="unicode", pages=[Page(page_number=1, width=600, height=800, elements=[_paragraph(text)])]
    )
    chunks = DocumentChunker.chunk(doc, tokenizer="cl100k_base", max_tokens=32, overlap_tokens=6)
    assert all(chunk.token_estimate == exact(chunk.text) <= 32 for chunk in chunks)
    assert all(chunk.tokenizer == "cl100k_base" and not chunk.budget_exceeded for chunk in chunks)


def test_text_and_geometry_are_both_required_for_ocr_deduplication():
    box = BoundingBox(x0=50, y0=50, x1=250, y1=80)
    native = [
        RawTextBlock(
            bbox=box, text="INVOICE ID 123", spans=[], avg_font_size=11, is_bold=False, page_number=1
        )
    ]
    lines = [
        OCRLine(text="INVOICE", bbox=box, confidence=0.9),
        OCRLine(text="INVOICE", bbox=BoundingBox(x0=50, y0=250, x1=250, y1=280), confidence=0.9),
    ]
    assert missing_lines(lines, native) == [lines[1]]


@pytest.mark.skipif(not OCREngine.is_available(), reason="Local OCR extra required")
@pytest.mark.parametrize("mode", ["sdk", "api"])
def test_mixed_page_recovers_only_uncovered_region(monkeypatch, mode):
    if not _ocr_ready():
        pytest.skip("OCR backend cannot initialize")
    gold = TRUTH["mixed_scan"]
    image = Image.new("RGB", (1600, 300), "white")
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:
        font = ImageFont.load_default()
    ImageDraw.Draw(image).text((40, 90), gold["scanned"], fill="black", font=font)
    stream = BytesIO()
    image.save(stream, format="PNG")
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((54, 90), gold["native"], fontsize=12)
    page.insert_image(fitz.Rect(54, 180, 554, 300), stream=stream.getvalue())
    page.insert_text((54, 380), gold["footer"], fontsize=12)
    raw = document.tobytes()
    document.close()
    crop = Mock(wraps=PDFRenderer.render_region_to_bytes)
    monkeypatch.setattr(PDFRenderer, "render_region_to_bytes", crop)
    monkeypatch.setattr(
        PDFRenderer, "render_page_to_bytes", Mock(side_effect=AssertionError("full-page OCR is unnecessary"))
    )
    if mode == "sdk":
        result = parse(
            raw, formats=["text", "json"], images="none", ocr="auto", json_options={"coordinates": True}
        )
        assert crop.call_count == 1
    else:
        from fastapi.testclient import TestClient

        from trueparse import ParseResult
        from trueparse.api.app import app

        response = TestClient(app).post(
            "/v1/parse",
            files={"file": ("mixed.pdf", raw)},
            data={
                "options": json.dumps(
                    {"formats": ["text", "json"], "ocr": "auto", "json_options": {"coordinates": True}}
                )
            },
        )
        assert response.status_code == 200, response.text
        result = ParseResult.model_validate(response.json())
    assert result.text.count(gold["native"]) == result.text.count(gold["footer"]) == 1
    assert "SCANNED" in result.text.upper() and "1234" in result.text
    ocr = [element for element in result.document["nodes"].values() if element["method"] == "ocr_model"]
    assert ocr and all(180 <= element["source"][0]["bbox"][1] <= 300 for element in ocr)


@pytest.mark.skipif(not OCREngine.is_available(), reason="Local OCR extra required")
def test_ocr_recovers_a_native_text_stage_failure(scanned_pdf_path, monkeypatch):
    if not _ocr_ready():
        pytest.skip("OCR backend cannot initialize")
    monkeypatch.setattr(
        NativeExtractor, "extract_page_text_blocks", Mock(side_effect=RuntimeError("broken text decoder"))
    )
    result = parse(scanned_pdf_path, formats=["text"], ocr="auto")
    assert "INVOICE" in result.text.upper()
    assert any("recovered with OCR" in warning for warning in result.warnings)


@pytest.mark.parametrize("mode", ["sdk", "api"])
def test_exact_counter_is_wired_through_public_request(sample_pdf_path, mode):
    pytest.importorskip("tiktoken")
    if not encoding_path().is_file():
        pytest.skip("Provision tokenizer data during setup")
    options = {
        "formats": ["chunks"],
        "chunk_tokenizer": "cl100k_base",
        "chunk_size": 32,
        "ocr": "never",
        "max_pages": 1,
    }
    if mode == "sdk":
        payload = parse(sample_pdf_path, **options).model_dump(mode="json")
    else:
        from fastapi.testclient import TestClient

        from trueparse.api.app import app

        response = TestClient(app).post(
            "/v1/parse",
            files={"file": (sample_pdf_path.name, sample_pdf_path.read_bytes())},
            data={"options": json.dumps(options)},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
    count = token_counter("cl100k_base")
    assert all(
        chunk["tokenizer"] == "cl100k_base" and chunk["token_estimate"] == count(chunk["text"]) <= 32
        for chunk in payload["data"]["chunks"]
    )
    assert payload["metrics"]["stage_calls"]["native_text"] == 1
    assert payload["metrics"]["stage_timings_ms"]["chunking"] > 0
