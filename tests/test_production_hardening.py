import json
from unittest.mock import Mock

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from trueparse import CanonicalDocument, ParseError, ResourceLimits, get_schema, parse
from trueparse.api.app import app
from trueparse.chunking.tokenization import encoding_path, token_counter
from trueparse.cli.main import app as cli
from trueparse.config.options import JsonOptions, ParseOptions
from trueparse.contracts.document import Scope
from trueparse.exporters.html import HTMLExporter
from trueparse.exporters.markdown import MarkdownExporter
from trueparse.exporters.projection import project_document
from trueparse.extraction.ocr.engine import OCREngine
from trueparse.extraction.pdf.renderer import PDFRenderer
from trueparse.extraction.pdf.text import NativeExtractor
from trueparse.models.document import Document, DocumentMetadata, Page
from trueparse.models.elements import DocumentElement
from trueparse.pipeline.runner import PDFParser
from trueparse.processing.classification import TextClassifier
from trueparse.processing.lists import parse_list
from trueparse.processing.text_merge import merge_paragraphs
from trueparse.runtime.budget import WorkBudget


def test_shared_result_factory_preserves_partial_status(sample_pdf_path, monkeypatch):
    original = NativeExtractor.extract_page_text_blocks

    def failure(page, page_number):
        if page_number == 1:
            raise RuntimeError("controlled failure")
        return original(page, page_number)

    monkeypatch.setattr(NativeExtractor, "extract_page_text_blocks", failure)
    internal = PDFParser(
        ParseOptions(ocr="never", output_config={"save_to_disk": False, "formats": ["json"]})
    ).parse_to_response(sample_pdf_path)
    public = parse(sample_pdf_path, ocr="never", formats=["json"])
    assert internal.status == internal.document["status"] == public.status == "partial"
    assert internal.scope == public.scope
    assert internal.issues == public.issues
    assert internal.representations == public.representations


def test_strict_sdk_and_internal_reject_incomplete(scanned_pdf_path):
    for call in (
        lambda: parse(scanned_pdf_path, ocr="never", strict=True),
        lambda: PDFParser(
            ParseOptions(ocr="never", strict=True, output_config={"save_to_disk": False})
        ).parse_to_response(scanned_pdf_path),
    ):
        with pytest.raises(ParseError) as error:
            call()
        assert error.value.code.value == "EXTRACTION_INCOMPLETE"


def test_strict_http_and_cli_reject_incomplete(scanned_pdf_path):
    response = TestClient(app).post(
        "/v1/parse",
        files={"file": ("scan.pdf", scanned_pdf_path.read_bytes())},
        data={"options": json.dumps({"ocr": "never", "strict": True})},
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "EXTRACTION_INCOMPLETE"
    result = CliRunner().invoke(cli, ["parse", str(scanned_pdf_path), "--ocr", "never", "--strict"])
    assert result.exit_code == 1 and "Strict parsing" in result.output


@pytest.mark.parametrize(
    "name", ["max_asset_bytes", "max_render_pixels", "max_response_bytes", "max_nodes", "max_table_cells"]
)
def test_resource_limits_raise_explicit_errors(name, sample_pdf_path):
    with pytest.raises(ParseError) as error:
        parse(sample_pdf_path, formats=["json"], images="base64", ocr="never", limits={name: 1})
    assert error.value.code.value == "PDF_RESOURCE_LIMIT"


def test_ocr_render_limit_is_checked_before_allocation(scanned_pdf_path, monkeypatch):
    monkeypatch.setattr(OCREngine, "is_available", lambda: True)
    renderer = Mock(side_effect=AssertionError("Rendering must not start"))
    monkeypatch.setattr(PDFRenderer, "render_page_to_bytes", renderer)
    with pytest.raises(ParseError) as error:
        parse(scanned_pdf_path, ocr="always", limits={"max_render_pixels": 1})
    assert error.value.code.value == "PDF_RESOURCE_LIMIT"
    renderer.assert_not_called()


def test_render_and_ocr_budgets_accumulate():
    with fitz.open() as pdf:
        page = pdf.new_page(width=100, height=100)
        budget = WorkBudget(ResourceLimits(max_total_render_pixels=15000))
        budget.reserve_render(page, 72)
        with pytest.raises(ParseError):
            budget.reserve_render(page, 72)
        budget = WorkBudget(ResourceLimits(max_ocr_pages=1))
        budget.reserve_render(page, 72, ocr_page=1)
        budget.reserve_render(page, 72, ocr_page=1)
        with pytest.raises(ParseError):
            budget.reserve_render(page, 72, ocr_page=2)


def test_http_limits_can_be_lowered_but_not_raised(sample_pdf_path):
    client = TestClient(app)
    for ceiling, status, code in [
        (128, 413, "PDF_RESOURCE_LIMIT"),
        (256 * 1024 * 1024, 422, "INVALID_OPTIONS"),
    ]:
        response = client.post(
            "/v1/parse",
            files={"file": ("report.pdf", sample_pdf_path.read_bytes())},
            data={"options": json.dumps({"ocr": "never", "limits": {"max_response_bytes": ceiling}})},
        )
        assert response.status_code == status and response.json()["error"]["code"] == code


def test_response_byte_limit_is_exact(sample_pdf_path):
    result = parse(sample_pdf_path, formats=["json"], ocr="never")
    assert len(result.model_dump_json().encode("utf-8")) < ResourceLimits().max_response_bytes
    with pytest.raises(ParseError):
        parse(sample_pdf_path, formats=["json"], ocr="never", limits={"max_response_bytes": 256})


def test_parser_reuse_resets_reports_and_work_counters(sample_pdf_path):
    parser = PDFParser(ParseOptions(ocr="never", output_config={"save_to_disk": False, "formats": ["json"]}))
    parser.parse_to_response(sample_pdf_path)
    parser.options.output_config.formats = ["text"]
    result = parser.parse_to_response(sample_pdf_path)
    assert set(result.representations) == {"text"}
    assert parser.budget.asset_bytes == 0


def test_raw_normalization_map_survives_page_merge():
    first = DocumentElement(
        id="a", type="paragraph", page=1, reading_order=1, content="An inter-\nnational docu-"
    )
    second = DocumentElement(
        id="b", type="paragraph", page=2, reading_order=1, content="ment continues here."
    )
    pages = merge_paragraphs([[first], [second]])
    document = Document(
        id="mapping",
        metadata=DocumentMetadata(page_count=2),
        scope=Scope(source_pages=2, requested_pages=[1, 2], processed_pages=[1, 2]),
        pages=[
            Page(page_number=i + 1, width=612, height=792, elements=elements)
            for i, elements in enumerate(pages)
        ],
    )
    canonical = project_document(document, JsonOptions())
    node = canonical.nodes["a"]
    assert node.content == "An international document continues here."
    assert node.source_texts == {"a": "An inter-\nnational docu-", "b": "ment continues here."}
    assert {fragment.page for fragment in node.source} == {1, 2}
    assert node.normalization_map
    assert (
        CanonicalDocument.model_validate_json(canonical.model_dump_json()).nodes["a"].content == node.content
    )
    broken = canonical.model_dump()
    broken["nodes"]["a"]["normalization_map"][0]["source_start"] = 1
    with pytest.raises(ValidationError):
        CanonicalDocument.model_validate(broken)


def test_nested_lists_use_one_parser_across_json_markdown_html():
    content = "3. Parent\n    - Child one\n        1. Grandchild\n    - Child two\n4. Next parent"
    items = parse_list(content)
    assert len(items) == 2 and len(items[0].children) == 2
    assert items[0].children[0].children[0].text == "Grandchild"
    element = DocumentElement(id="list", type="list", page=1, reading_order=1, content=content)
    document = Document(
        id="list",
        metadata=DocumentMetadata(page_count=1),
        scope=Scope(source_pages=1, requested_pages=[1], processed_pages=[1]),
        pages=[Page(page_number=1, width=612, height=792, elements=[element])],
    )
    node = project_document(document, JsonOptions()).nodes["list"]
    assert node.items == items
    assert "    - Child one" in MarkdownExporter.export(document)
    assert '<ol><li value="3">Parent<ul>' in HTMLExporter.export(document)
    assert '<li value="1">Grandchild</li>' in HTMLExporter.export(document)


@pytest.mark.parametrize("encoding", ["cl100k_base", "o200k_base"])
def test_exact_encodings_are_offline_and_match_reference(encoding, monkeypatch):
    tiktoken = pytest.importorskip("tiktoken")
    if not encoding_path(encoding).is_file():
        pytest.skip("Provision the tokenizer during setup")
    reference = tiktoken.get_encoding(encoding)
    monkeypatch.setattr(
        "urllib.request.urlopen", Mock(side_effect=AssertionError("No downloads during parsing"))
    )
    text = "Customer ग्राहक 客户 café 👩🏽‍💻 total=123.45 <|endoftext|>"
    assert token_counter(encoding)(text) == len(reference.encode(text, disallowed_special=()))


def test_unknown_schema_name_is_rejected():
    with pytest.raises(ValueError):
        get_schema("../private")


def test_ocr_initialization_failure_can_be_retried(monkeypatch):
    from types import SimpleNamespace

    from trueparse.extraction.ocr import engine

    backend = Mock(side_effect=[RuntimeError("temporary setup failure"), object()])
    monkeypatch.setattr(engine, "_ENGINE", None)
    monkeypatch.setattr(engine, "_ENGINE_KIND", None)
    monkeypatch.setattr(engine, "_installed_backend", lambda: "rapidocr")
    monkeypatch.setattr(engine.importlib, "import_module", lambda name: SimpleNamespace(RapidOCR=backend))
    assert engine._load_engine() == (None, None)
    assert engine._load_engine()[0] is not None


def test_classification_failure_retains_raw_text_and_source(sample_pdf_path, monkeypatch):
    original = TextClassifier.classify_and_build_elements

    def fail(*args, **kwargs):
        if "test paragraph" in kwargs["raw_blocks"][0].text:
            raise RuntimeError("controlled classification failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(TextClassifier, "classify_and_build_elements", fail)
    result = parse(sample_pdf_path, formats=["json", "text"], ocr="never")
    node = next(node for node in result.iter_nodes() if node.role == "unknown")
    assert "test paragraph" in node.content and node.source[0].page == 1
    assert node.recovery_reason == "classification_failed"
    assert "test paragraph" in result.text and result.status == "partial"
    assert any(node.id in issue.node_ids for issue in result.issues)


def test_server_resource_defaults_apply_without_explicit_client_limits(sample_pdf_path, monkeypatch):
    from trueparse.api import routes

    monkeypatch.setattr(routes, "_resource_ceilings", ResourceLimits(max_nodes=1))
    response = TestClient(app).post(
        "/v1/parse",
        files={"file": ("sample.pdf", sample_pdf_path.read_bytes())},
        data={"options": json.dumps({"ocr": "never", "formats": ["json"]})},
    )
    assert response.status_code == 413 and response.json()["error"]["code"] == "PDF_RESOURCE_LIMIT"


@pytest.mark.parametrize("role", ["figure", "image", "chart", "diagram"])
def test_every_visual_role_has_a_marker_when_bytes_are_absent(role):
    from trueparse.exporters.text import TextExporter

    element = DocumentElement(id="visual", type=role, page=1, reading_order=1, content="")
    document = Document(id="visual", pages=[Page(page_number=1, width=612, height=792, elements=[element])])
    assert "TrueParse visual:" in MarkdownExporter.export(document)
    assert "TrueParse visual:" in HTMLExporter.export(document)
    assert "TrueParse visual:" in TextExporter.export(document)


def test_unused_image_resources_do_not_create_phantom_occurrences(tmp_path):
    from io import BytesIO

    from PIL import Image

    from trueparse.extraction.pdf.images import ImageExtractor

    image = BytesIO()
    Image.new("RGB", (40, 40), "blue").save(image, format="PNG")
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_image(fitz.Rect(50, 50, 90, 90), stream=image.getvalue())
        image_stream = page.get_contents()[0]
        page.insert_text((50, 180), "Only this text is visible.")
        pdf.update_stream(image_stream, b"")
        assert page.get_images() and not page.get_image_info()
        assert not ImageExtractor.extract_embedded_images(pdf, include_bytes=False)
        path = tmp_path / "unused-resource.pdf"
        pdf.save(path)
    result = parse(path, formats=["json"], ocr="never")
    assert not [node for node in result.iter_nodes() if node.type == "visual"]


@pytest.mark.parametrize("page_count", [1, 2])
def test_images_inside_tables_are_preserved_and_linked(tmp_path, page_count):
    from io import BytesIO

    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (20, 20), "green").save(image, format="PNG")
    path = tmp_path / "table-image.pdf"
    with fitz.open() as pdf:
        for index in range(page_count):
            page = pdf.new_page()
            if index:
                page.insert_text((50, 70), "Financial table continued")
            for y in (100, 140, 180):
                page.draw_line((50, y), (450, y))
            for x in (50, 250, 450):
                page.draw_line((x, 100), (x, 180))
            page.insert_text((60, 125), "Item")
            page.insert_text((260, 125), "Value")
            page.insert_text((60, 165), "Status")
            page.insert_text((260, 165), "Approved")
            page.insert_image(fitz.Rect(200, 150, 220, 170), stream=image.getvalue())
        pdf.save(path)
    result = parse(path, formats=["json", "markdown", "text"], images="base64", ocr="never")
    nodes = list(result.iter_nodes())
    table = next(node for node in nodes if node.type == "table")
    visuals = [node for node in nodes if node.type == "visual"]
    assert len(visuals) == page_count
    assert all(visual.parent_table_id == table.id for visual in visuals)
    assert all(result.get_asset(visual.asset_id) for visual in visuals)
    assert "data:image/" in result.markdown
    assert "TrueParse visual:" in result.text


@pytest.mark.parametrize("body", [b"not-json", b'{"unexpected":"shape"}'])
def test_client_rejects_malformed_results_with_public_error(sample_pdf_path, body):
    import httpx

    from trueparse import TrueParseClient

    with TrueParseClient() as client:
        client._http.close()
        client._http = httpx.Client(
            base_url="http://test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
        )
        with pytest.raises(ParseError) as error:
            client.parse(sample_pdf_path)
        assert error.value.code.value == "SERIALIZATION_ERROR"


def test_client_bounds_decoded_response_size(sample_pdf_path):
    import httpx

    from trueparse import TrueParseClient

    with TrueParseClient() as client:
        client._http.close()
        client._http = httpx.Client(
            base_url="http://test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 1024)),
        )
        with pytest.raises(ParseError) as error:
            client.parse(sample_pdf_path, limits={"max_response_bytes": 128})
        assert error.value.code.value == "PDF_RESOURCE_LIMIT"


def test_table_vector_marks_are_typed_annotations_not_inferred_values(tmp_path):
    path = tmp_path / "vector-cell.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        for y in (100, 140, 180):
            page.draw_line((50, y), (450, y))
        for x in (50, 250, 450):
            page.draw_line((x, 100), (x, 180))
        page.insert_text((60, 125), "Item")
        page.insert_text((260, 125), "Value")
        page.insert_text((60, 165), "Status")
        page.draw_rect(fitz.Rect(280, 151, 290, 161), fill=(0, 0, 0))
        pdf.save(path)
    result = parse(path, formats=["json", "text"], ocr="never")
    table = next(node for node in result.iter_nodes() if node.type == "table")
    cell = next(cell for cell in table.cells if cell.kind == "visual_marker")
    assert cell.text == "[TrueParse vector mark]"
    assert "[X]" not in result.text
