import copy
import io
import json
import re
import unicodedata
from pathlib import Path

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from PIL import Image
from pydantic import ValidationError

from trueparse import CanonicalDocument, parse
from trueparse.api.app import app
from trueparse.contracts.schema import build_schema, get_schema
from trueparse.exporters.html import HTMLExporter
from trueparse.exporters.markdown import MarkdownExporter
from trueparse.exporters.text import TextExporter
from trueparse.extraction.pdf.text import NativeExtractor
from trueparse.models.document import Document, DocumentMetadata, Page
from trueparse.models.elements import DocumentElement

TRUTH = json.loads((Path(__file__).parent / "fixtures/conversion_truth.json").read_text(encoding="utf-8-sig"))


@pytest.fixture
def conversion_pdf(tmp_path):
    destination = tmp_path / "conversion.pdf"
    image = io.BytesIO()
    Image.new("RGB", (100, 60), (41, 97, 127)).save(image, format="PNG")
    with fitz.open() as pdf:
        first = pdf.new_page(width=612, height=792)
        for x, y, text, size in [
            (54, 25, "NORTHWIND INTERNAL", 9),
            (54, 72, "Quarterly results", 18),
            (54, 125, "Revenue rose to $123.45 USD.", 11),
            (54, 165, "3. Apples", 11),
            (54, 185, "4. Pears", 11),
            (54, 310, "Figure 1. Regional emblem.", 10),
            (54, 775, "Page 1 of 2", 9),
        ]:
            first.insert_text((x, y), text, fontsize=size)
        first.insert_image(fitz.Rect(54, 220, 154, 280), stream=image.getvalue())
        second = pdf.new_page(width=612, height=792)
        second.insert_text((54, 72), "Financial summary", fontsize=18)
        for y in (100, 140, 180, 220):
            second.draw_line((54, y), (500, y))
        for x in (54, 260, 500):
            second.draw_line((x, 100), (x, 220))
        for row, values in enumerate(TRUTH["table"]):
            for column, value in enumerate(values):
                second.insert_text((60 + 210 * column, 125 + 40 * row), value, fontsize=11)
        second.insert_image(fitz.Rect(54, 350, 154, 410), stream=image.getvalue())
        second.insert_text((54, 775), "Page 2 of 2", fontsize=9)
        pdf.set_metadata({"title": TRUTH["metadata_title"]})
        pdf.save(destination)
    return destination


def normalized(text):
    return " ".join(unicodedata.normalize("NFC", text).split())


@pytest.mark.parametrize("mode", ["sdk", "api"])
def test_known_content_order_cells_and_visuals(conversion_pdf, mode):
    options = {"formats": ["json", "markdown", "html", "text", "chunks"], "ocr": "never", "images": "base64"}
    if mode == "sdk":
        result = parse(conversion_pdf, **options).model_dump(mode="json")
    else:
        response = TestClient(app).post(
            "/v1/parse",
            files={"file": (conversion_pdf.name, conversion_pdf.read_bytes())},
            data={"options": json.dumps(options)},
        )
        assert response.status_code == 200, response.text
        result = response.json()
    Draft202012Validator(get_schema("response")).validate(result)
    document = CanonicalDocument.model_validate(result["data"]["json"])
    nodes = list(document.iter_nodes())
    assert document.schema_version == "3.0"
    assert document.source.page_count == 2
    assert document.scope.processed_pages == [1, 2]
    assert len([node for node in nodes if node.type == "visual"]) == TRUTH["visual_occurrences"]
    table = next(node for node in nodes if node.type == "table")
    assert [[c.text for c in table.cells if c.row == row] for row in range(table.rows)] == TRUTH["table"]
    for fmt in ("markdown", "html", "text"):
        output = normalized(result["data"][fmt])
        assert (
            TRUTH["metadata_title"] not in re.sub(r"<head>.*?</head>", "", output)
            if fmt == "html"
            else TRUTH["metadata_title"] not in output
        )
        assert "Revenue" in output and "24.5%" in output
    text = normalized(result["data"]["text"])
    cursor = 0
    for value in TRUTH["expected_text"]:
        position = text.find(normalized(value), cursor)
        assert position >= cursor, value
        cursor = position + len(normalized(value))
    for report in result["representations"].values():
        assert set(report["rendered_node_ids"]) | set(report["excluded_node_ids"]) == set(document.nodes)


def test_format_independence_and_omitted_images(conversion_pdf):
    combined = parse(conversion_pdf, formats=["json", "markdown", "html", "text", "chunks"], ocr="never")
    assert any(n.type == "visual" and n.availability == "omitted" for n in combined.iter_nodes())
    assert all(a["availability"] == "omitted" for a in combined.document["assets"].values())
    for fmt in ("text", "markdown", "html", "chunks"):
        alone = parse(conversion_pdf, formats=[fmt], ocr="never")
        assert getattr(alone, fmt) == getattr(combined, fmt)
    full = parse(conversion_pdf, formats=["json"], images="base64", ocr="never")
    assert list(full.document["nodes"]) == list(combined.document["nodes"])
    assert full.document["extraction_id"] == combined.document["extraction_id"]


@pytest.mark.parametrize(
    "mutation", ["duplicate_order", "missing_node", "bad_asset", "invalid_span", "overlap", "future_version"]
)
def test_structural_errors_are_rejected(conversion_pdf, mutation):
    document = copy.deepcopy(parse(conversion_pdf, formats=["json"], images="base64", ocr="never").document)
    table = next(n for n in document["nodes"].values() if n["type"] == "table")
    if mutation == "duplicate_order":
        document["reading_order"].append(document["reading_order"][0])
    elif mutation == "missing_node":
        document["reading_order"].pop()
    elif mutation == "bad_asset":
        document["assets"].clear()
    elif mutation == "invalid_span":
        table["cells"][0]["col_span"] = table["columns"] + 1
    elif mutation == "overlap":
        table["cells"][1]["column"] = 0
    else:
        document["schema_version"] = "99.0"
    with pytest.raises(ValidationError):
        CanonicalDocument.model_validate(document)


def test_projection_is_valid_and_exclusions_explicit(conversion_pdf):
    result = parse(
        conversion_pdf,
        formats=["json"],
        images="base64",
        ocr="never",
        json_options={
            "text_only": True,
            "coordinates": False,
            "hierarchy": False,
            "metadata": False,
            "tables": False,
        },
    )
    document = CanonicalDocument.model_validate(result.document)
    assert document.projection.excluded_node_ids
    assert "coordinates" in document.projection.omitted_fields
    assert not document.assets
    Draft202012Validator(get_schema()).validate(result.document)


def test_known_failure_is_partial_and_blank_page_is_valid(conversion_pdf, tmp_path, monkeypatch):
    original = NativeExtractor.extract_page_text_blocks

    def fail_first(page, page_number):
        if page_number == 1:
            raise RuntimeError("controlled failure")
        return original(page, page_number)

    monkeypatch.setattr(NativeExtractor, "extract_page_text_blocks", fail_first)
    result = parse(conversion_pdf, formats=["json", "text"], ocr="never")
    assert result.status == "partial" and result.document["status"] == "partial"
    assert result.scope.failed_pages == [1]
    assert any(i.code == "TEXT_EXTRACTION_FAILED" and i.page == 1 for i in result.issues)
    assert "Margin" in result.text
    monkeypatch.undo()
    blank = tmp_path / "blank.pdf"
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.save(blank)
    empty = parse(blank, formats=["json"], ocr="never")
    assert empty.status == "completed" and not empty.document["nodes"]


@pytest.mark.parametrize("name", ["document", "request", "response", "chunk"])
def test_bundled_schemas_match_models(name):
    schema = get_schema(name)
    Draft202012Validator.check_schema(schema)
    assert schema == build_schema(name)


def test_preserves_footer_metadata_and_literal_syntax():
    doc = Document(
        id="literal",
        metadata=DocumentMetadata(title="METADATA"),
        pages=[
            Page(
                page_number=1,
                width=612,
                height=792,
                elements=[
                    DocumentElement(
                        id="body",
                        type="paragraph",
                        page=1,
                        reading_order=1,
                        content="[example](javascript:bad) **literal**",
                    ),
                    DocumentElement(
                        id="footer", type="footer", page=1, reading_order=2, content="Keep this footer."
                    ),
                ],
            )
        ],
    )
    assert "\\[example\\]" in MarkdownExporter.export(doc)
    for render in (MarkdownExporter.export, HTMLExporter.export, TextExporter.export):
        output = render(doc)
        assert "Keep this footer." in output
    assert "METADATA" not in MarkdownExporter.export(doc)
    assert "METADATA" not in TextExporter.export(doc)


def test_omitted_images_do_not_decode_payloads(conversion_pdf, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected image decoding")

    monkeypatch.setattr(fitz.Document, "extract_image", forbidden)
    result = parse(conversion_pdf, formats=["json", "text"], images="none", ocr="never")
    visuals = [n for n in result.iter_nodes() if n.type == "visual"]
    assert len(visuals) == 2 and all(n.availability == "omitted" for n in visuals)
    assert result.status == "completed"


def test_asset_failure_preserves_occurrence_and_reports_partial(conversion_pdf, monkeypatch):
    def failure(*args, **kwargs):
        raise RuntimeError("controlled asset failure")

    monkeypatch.setattr(fitz.Document, "extract_image", failure)
    result = parse(conversion_pdf, formats=["json", "markdown"], images="base64", ocr="never")
    visuals = [n for n in result.iter_nodes() if n.type == "visual"]
    assert len(visuals) == 2 and all(n.availability == "unavailable" for n in visuals)
    assert result.status == "partial"
    assert any(i.code == "ASSET_EXTRACTION_INCOMPLETE" for i in result.issues)
    assert "TrueParse visual:" in result.markdown


def test_vector_failure_preserves_source_region(tmp_path, monkeypatch):
    from trueparse.extraction.pdf.renderer import PDFRenderer

    path = tmp_path / "vector.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.draw_rect(fitz.Rect(50, 100, 200, 250), fill=(0, 0, 1))
        page.insert_text((50, 350), "Body content remains.")
        pdf.save(path)

    def failure(*args, **kwargs):
        raise RuntimeError("controlled crop failure")

    monkeypatch.setattr(PDFRenderer, "render_region_to_bytes", failure)
    result = parse(path, formats=["json", "text"], images="base64", ocr="never")
    assert result.status == "partial"
    assert any(n.type == "visual" and n.availability == "unavailable" for n in result.iter_nodes())
    assert "Body content remains." in result.text
