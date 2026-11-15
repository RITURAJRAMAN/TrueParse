"""Developer-facing journeys through the supported SDK."""

import asyncio
import json
import multiprocessing
from io import BytesIO

import pytest
from pydantic import ValidationError

from trueparse import JsonProjection, ParseError, ParseRequest, aparse, parse


def test_one_call_returns_markdown_without_files(sample_pdf_path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = parse(sample_pdf_path, ocr="never")
    assert "Revenue" in result.markdown
    assert result.document is None
    assert result.chunks is None
    assert result.page_count == 3
    assert not list(tmp_path.iterdir())


def test_explicit_formats_and_json_only_projection(sample_pdf_path):
    result = parse(
        sample_pdf_path,
        formats=["json", "markdown"],
        images="base64",
        ocr="never",
        json_options={"text_only": True, "coordinates": False},
    )
    assert result.document["assets"] == {}
    assert "data:image/" in result.markdown
    assert all("bbox" not in element for element in result.document["nodes"].values())
    assert set(result.model_dump(mode="json")["data"]) == {"json", "markdown", "text", "html", "chunks"}


def test_images_scope_is_explicit(sample_pdf_path):
    with pytest.raises(ParseError, match="images"):
        parse(sample_pdf_path, json_options={"images": "none"})
    assert JsonProjection().coordinates is False


@pytest.mark.parametrize(
    "options",
    [
        {"formats": []},
        {"formats": ["csv"]},
        {"chunk_size": 1},
        {"chunk_overlap": -1},
        {"max_pages": 0},
        {"timeout_seconds": -1},
        {"timeout_seconds": float("inf")},
        {"save_to_disk": True},
        {"json_options": {"tables": {"htlm": True}}},
    ],
)
def test_strict_request_validation(options):
    with pytest.raises(ValidationError):
        ParseRequest.model_validate(options)


def test_bytes_and_stream_input_preserve_position(sample_pdf_path):
    raw = sample_pdf_path.read_bytes()
    stream = BytesIO(raw)
    result = parse(stream, filename="Quarterly report.pdf", ocr="never")
    assert stream.tell() == 0
    assert result.source_file == "Quarterly report.pdf"
    assert parse(raw, ocr="never").document_id == result.document_id


def test_save_is_explicit_and_refuses_mixing_runs(sample_pdf_path, tmp_path):
    result = parse(
        sample_pdf_path,
        formats=["json", "markdown", "chunks"],
        images="path",
        ocr="never",
        timeout_seconds=30,
    )
    target = result.save(tmp_path / "export")
    assert (target / "sample_document.json").exists()
    assert (target / "sample_document.md").exists()
    assert (target / "sample_document.chunks.jsonl").exists()
    assert not (target / "output").exists()
    for asset_id, asset in result.document["assets"].items():
        assert (target / asset["path"]).read_bytes() == result.get_asset(asset_id)
    before = {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    with pytest.raises(FileExistsError):
        parse(sample_pdf_path, formats=["text"], ocr="never").save(target)
    assert before == {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()}


def test_save_base64_has_no_asset_folders(sample_pdf_path, tmp_path):
    result = parse(sample_pdf_path, formats=["json", "markdown"], images="base64", ocr="never")
    folder = result.save(tmp_path / "embedded")
    assert {path.name for path in folder.iterdir()} == {"sample_document.json", "sample_document.md"}
    restored = type(result).model_validate(json.loads(result.model_dump_json()))
    asset_id = next(iter(result.document["assets"]))
    assert restored.get_asset(asset_id) == result.get_asset(asset_id)


def test_serialized_path_results_cannot_silently_save_broken_links(sample_pdf_path, tmp_path):
    result = parse(sample_pdf_path, formats=["json"], images="path", ocr="never")
    restored = type(result).model_validate_json(result.model_dump_json())
    with pytest.raises(ValueError, match="binary data is missing"):
        restored.save(tmp_path / "missing-assets")
    assert not (tmp_path / "missing-assets").exists()


def test_http_connection_errors_use_the_public_error_type(sample_pdf_path, monkeypatch):
    import httpx

    from trueparse import TrueParseClient

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("service offline")

    with TrueParseClient() as client:
        monkeypatch.setattr(client._http, "stream", unavailable)
        with pytest.raises(ParseError) as error:
            client.parse(sample_pdf_path)
    assert error.value.code.value == "SERVICE_UNAVAILABLE"
    assert error.value.retryable


def test_hierarchy_projection_leaves_no_section_edges(sample_pdf_path):
    result = parse(sample_pdf_path, formats=["json"], ocr="never", json_options={"hierarchy": False})
    assert "sections" not in result.document
    assert all(
        not edge["source_id"].startswith("sec_") and not edge["target_id"].startswith("sec_")
        for edge in result.document["relationships"]
    )


def test_real_process_timeout_cleans_up(sample_pdf_path):
    children = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(ParseError) as error:
        parse(sample_pdf_path, timeout_seconds=0.001)
    assert error.value.code.value == "PARSE_TIMEOUT"
    assert {child.pid for child in multiprocessing.active_children()} == children
    assert parse(sample_pdf_path, ocr="never").markdown


def test_supervised_parse_and_async_helper(sample_pdf_path):
    assert parse(sample_pdf_path, timeout_seconds=30, ocr="never").page_count == 3
    assert asyncio.run(aparse(sample_pdf_path, ocr="never")).page_count == 3


def test_errors_and_warnings_are_visible_without_json(sample_pdf_path, tmp_path, monkeypatch):
    from trueparse.extraction.pdf.text import NativeExtractor

    original = NativeExtractor.extract_page_text_blocks

    def fail(*args, **kwargs):
        if kwargs.get("page_number") == 2:
            raise RuntimeError("Unreadable page")
        return original(*args, **kwargs)

    monkeypatch.setattr(NativeExtractor, "extract_page_text_blocks", fail)
    result = parse(sample_pdf_path, ocr="never")
    assert result.document is None
    assert any("Page 2" in warning for warning in result.warnings)
    assert "Conclusion" in result.markdown
    with pytest.raises(ParseError):
        parse(tmp_path / "missing.pdf")


def test_vector_failure_is_returned_without_json(monkeypatch):
    import pymupdf as fitz

    from trueparse.extraction.pdf.renderer import PDFRenderer

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 70), "Vector graphic below.")
    page.draw_circle((180, 220), 50, color=(1, 0, 0))
    content = doc.tobytes()
    doc.close()

    def fail(*args, **kwargs):
        raise RuntimeError("crop unavailable")

    monkeypatch.setattr(PDFRenderer, "render_region_to_bytes", fail)
    result = parse(content, images="base64", ocr="never")
    assert result.document is None
    assert any("crop unavailable" in warning for warning in result.warnings)
