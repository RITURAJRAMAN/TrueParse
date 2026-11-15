"""Pipeline output and failure-isolation checks."""

import base64
import json
from io import BytesIO
from unittest.mock import Mock

import pymupdf as fitz
import pytest

from trueparse.chunking.chunker import DocumentChunker
from trueparse.config.options import JsonOptions, OutputConfig, ParseOptions
from trueparse.exceptions import PDFEngineError
from trueparse.exporters.html import HTMLExporter
from trueparse.exporters.json import DocumentProjector
from trueparse.exporters.markdown import MarkdownExporter
from trueparse.exporters.text import TextExporter
from trueparse.extraction.pdf.text import NativeExtractor
from trueparse.models.document import Document, Page
from trueparse.models.elements import TableCell, TableElement
from trueparse.models.enums import ErrorCode, OCRMode
from trueparse.models.geometry import BoundingBox
from trueparse.pipeline.runner import PDFParser
from trueparse.processing.classification import TextClassifier
from trueparse.processing.tables import TableMerger


def options(tmp_path, **output):
    return ParseOptions(output_path=str(tmp_path / "outputs"), output_config=OutputConfig(**output))


def test_only_requested_format_is_computed_and_written(sample_pdf_path, tmp_path, monkeypatch):
    for exporter in (MarkdownExporter, HTMLExporter):
        monkeypatch.setattr(exporter, "export", Mock(side_effect=AssertionError("unrequested exporter")))
    monkeypatch.setattr(DocumentChunker, "chunk", Mock(side_effect=AssertionError("unrequested chunks")))
    opts = options(tmp_path, formats=["text"])
    parser = PDFParser(opts)
    response = parser.parse_to_response(sample_pdf_path)
    assert response.formats_provided == ["text"]
    assert response.data.text
    assert response.data.document is None
    folder = tmp_path / "outputs" / response.document_id
    assert [p.name for p in folder.iterdir()] == ["sample_document.txt"]


def test_requested_export_runs_once_for_disk_and_response(sample_pdf_path, tmp_path, monkeypatch):
    spy = Mock(wraps=TextExporter.export)
    monkeypatch.setattr(TextExporter, "export", spy)
    parser = PDFParser(options(tmp_path, formats=["text"], json_options={"images": "none"}))
    response = parser.parse_to_response(sample_pdf_path)
    assert spy.call_count == 1
    assert (tmp_path / "outputs" / response.document_id / "sample_document.txt").read_text(
        encoding="utf-8"
    ) == response.data.text


def test_memory_base64_is_self_contained_and_creates_no_directories(sample_pdf_path, tmp_path):
    parser = PDFParser(
        options(
            tmp_path,
            save_to_disk=False,
            formats=["json", "markdown", "html"],
            json_options={"images": "base64"},
        )
    )
    response = parser.parse_to_response(BytesIO(sample_pdf_path.read_bytes()), original_filename="report.pdf")
    assets = response.data.document["assets"]
    assert assets
    for asset in assets.values():
        assert "path" not in asset
        assert base64.b64decode(asset["data_uri"].split(",", 1)[1])
    assert "data:image/" in response.data.markdown
    assert "data:image/" in response.data.html
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize("removed", ["preset", "emit_html", "emit_text", "emit_chunks"])
def test_retired_options_are_rejected(removed):
    from pydantic import ValidationError

    model = OutputConfig if removed == "preset" else ParseOptions
    with pytest.raises(ValidationError):
        model.model_validate({removed: True})


@pytest.mark.parametrize("stage", ["scan", "classification", "inspection"])
def test_bad_page_is_reported_and_later_pages_survive(stage, sample_pdf_path, tmp_path, monkeypatch):
    if stage == "inspection":
        original = fitz.Page.get_text

        def fail(page, *args, **kwargs):
            if page.number == 1:
                raise RuntimeError("damaged page stream")
            return original(page, *args, **kwargs)

        monkeypatch.setattr(fitz.Page, "get_text", fail)
    else:
        name = "extract_page_text_blocks" if stage == "scan" else "classify_and_build_elements"
        owner = NativeExtractor if stage == "scan" else TextClassifier
        original = getattr(owner, name)

        def fail(*args, **kwargs):
            number = kwargs.get("page_number")
            if stage == "classification":
                blocks = kwargs["raw_blocks"]
                number = blocks[0].page_number if blocks else None
            if number == 2:
                raise RuntimeError("damaged page stream")
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, fail)
    opts = options(tmp_path, save_to_disk=False, json_options={"images": "none"})
    opts.ocr = OCRMode.NEVER  # this test measures degradation without recovery
    parser = PDFParser(opts)
    doc = parser.parse(sample_pdf_path)
    assert len(doc.pages) == 3
    assert doc.pages[1].quality.unknown_ratio == 1.0
    assert any("Page 2" in warning and "damaged page" in warning for warning in doc.quality.warnings)
    assert any("Conclusion" in element.content for element in doc.pages[2].elements)


def test_deadline_aborts_before_writing(sample_pdf_path, tmp_path, monkeypatch):
    clock = Mock(side_effect=[0.0, 2.0])
    monkeypatch.setattr("trueparse.pipeline.runner.time.monotonic", clock)
    opts = options(tmp_path)
    opts.timeout_seconds = 1
    with pytest.raises(PDFEngineError) as error:
        PDFParser(opts).parse(sample_pdf_path)
    assert error.value.code == ErrorCode.PDF_RESOURCE_LIMIT
    assert "timeout" in error.value.message
    assert not (tmp_path / "outputs").exists()


def test_page_ceiling_applies_to_inspection_and_asset_occurrences(sample_pdf_path, tmp_path):
    opts = options(tmp_path, save_to_disk=False)
    opts.max_pages = 1
    doc = PDFParser(opts).parse(sample_pdf_path)
    assert len(doc.pages) == 1
    assert all(occ.page == 1 for asset in doc.assets.values() for occ in asset.occurrences)


@pytest.mark.parametrize("strategy", ["section", "token", "hybrid"])
def test_large_table_repeats_headers_and_retains_every_row(strategy):
    header = "| Metric | Value |\n| --- | --- |"
    rows = [f"| item_{i} | {i} |" for i in range(40)]
    table = TableElement(
        id="table",
        page=1,
        reading_order=1,
        content="table",
        rows=41,
        columns=2,
        markdown=header + "\n" + "\n".join(rows),
    )
    document = Document(id="doc", pages=[Page(page_number=1, width=600, height=800, elements=[table])])
    chunks = DocumentChunker.chunk(document, strategy=strategy, max_tokens=64, overlap_tokens=0)
    assert len(chunks) > 1
    assert all(chunk.text.startswith(header) for chunk in chunks)
    assert [row for chunk in chunks for row in chunk.text.splitlines()[2:]] == rows
    assert all(chunk.element_ids == ["table"] for chunk in chunks)


def test_three_page_table_continuation_has_continuous_rows_and_provenance():
    pages = []
    for page in range(1, 4):
        cells = [
            TableCell(
                id=f"{page}_{row}_{col}",
                row=row,
                column=col,
                text=("Metric", "Value")[col] if row == 0 else f"{page}_{col}",
                is_header=row == 0,
                bbox=BoundingBox(x0=50 + col * 100, y0=row * 20, x1=140 + col * 100, y1=row * 20 + 20),
            )
            for row in range(2)
            for col in range(2)
        ]
        pages.append(
            [
                TableElement(
                    id=f"t{page}",
                    page=page,
                    reading_order=1,
                    content="Metric\tValue\nbody",
                    rows=2,
                    columns=2,
                    cells=cells,
                    metadata={"continuation_hint": page > 1},
                )
            ]
        )
    TableMerger.merge_cross_page_tables(pages)
    assert pages[1:] == [[], []]
    table = pages[0][0]
    assert table.rows == 4
    assert sorted({cell.row for cell in table.cells}) == [0, 1, 2, 3]
    doc = Document(id="doc", pages=[Page(page_number=1, width=600, height=800, elements=[table])])
    assert DocumentChunker.chunk(doc)[0].page_end == 3


def test_projection_removes_page_metrics_and_supports_rechunking(sample_pdf_path, tmp_path):
    parser = PDFParser(options(tmp_path, save_to_disk=False))
    doc = parser.parse(sample_pdf_path)
    projected = DocumentProjector.project(
        doc, JsonOptions(coordinates=False, quality_scores=False, tables=False)
    )
    assert "bbox" not in json.dumps(projected)
    assert "diagnostics" not in projected
    from trueparse import CanonicalDocument

    assert list(CanonicalDocument.model_validate(projected).iter_nodes())
