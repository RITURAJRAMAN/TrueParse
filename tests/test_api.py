"""HTTP contract tests for the supported stateless service."""

import json

import pytest
from fastapi.testclient import TestClient

from trueparse import parse
from trueparse.api.app import app

client = TestClient(app)


def upload(path, options=None, **kwargs):
    return client.post(
        "/v1/parse",
        files={"file": (path.name, path.read_bytes(), "application/pdf")},
        data={"options": json.dumps(options or {})},
        **kwargs,
    )


def test_health_and_single_public_parse_route():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {"/health", "/v1/parse"}
    assert "ParseRequest" in schema["components"]["schemas"]


def test_api_and_sdk_outputs_match(sample_pdf_path):
    options = {"formats": ["markdown", "json", "chunks"], "images": "base64", "ocr": "never"}
    response = upload(sample_pdf_path, options)
    assert response.status_code == 200, response.text
    local = parse(sample_pdf_path, **options)
    assert response.json()["data"] == local.model_dump(mode="json")["data"]
    assert response.json()["warnings"] == local.warnings
    assert response.json()["source_file"] == sample_pdf_path.name


def test_raw_pdf_request(sample_pdf_path):
    response = client.post(
        "/v1/parse",
        content=sample_pdf_path.read_bytes(),
        headers={
            "Content-Type": "application/pdf",
            "X-Filename": "raw.pdf",
            "X-Parse-Options": '{"formats":["text"],"ocr":"never"}',
        },
    )
    assert response.status_code == 200, response.text
    assert "Revenue" in response.json()["data"]["text"]
    assert response.json()["source_file"] == "raw.pdf"


def test_large_upload_never_rolls_to_disk(sample_pdf_path, monkeypatch):
    import starlette.formparsers as formparsers

    created = []
    original = formparsers.SpooledTemporaryFile

    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        created.append(value)
        return value

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", capture)
    raw = sample_pdf_path.read_bytes() + b"\n" + b" " * (2 * 1024 * 1024)
    response = client.post(
        "/v1/parse",
        files={"file": ("large.pdf", raw)},
        data={"options": '{"formats":["text"],"ocr":"never"}'},
    )
    assert response.status_code == 200, response.text
    assert created and all(not file._rolled for file in created)
    assert all(file.closed for file in created)


@pytest.mark.parametrize(
    "options",
    [
        {"save_to_disk": True},
        {"formats": ["csv"]},
        {"images": "path"},
        {"chunk_size": 2},
        {"json_options": {"images": "none"}},
        {"preset": "rag_optimized"},
    ],
)
def test_invalid_and_competing_options_are_rejected(sample_pdf_path, options):
    response = upload(sample_pdf_path, options)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_OPTIONS"


def test_bad_json_and_media_type(sample_pdf_path):
    response = client.post(
        "/v1/parse", files={"file": ("doc.pdf", sample_pdf_path.read_bytes())}, data={"options": "broken"}
    )
    assert response.status_code == 422
    assert (
        client.post("/v1/parse", content=b"text", headers={"Content-Type": "text/plain"}).status_code == 415
    )


def test_authentication_before_upload(sample_pdf_path, monkeypatch):
    monkeypatch.setenv("TRUEPARSE_API_KEY", "test-secret")
    assert upload(sample_pdf_path).status_code == 401
    assert upload(sample_pdf_path, {"ocr": "never"}, headers={"X-API-Key": "test-secret"}).status_code == 200


def test_streaming_upload_ceiling(sample_pdf_path, monkeypatch):
    monkeypatch.setenv("TRUEPARSE_MAX_UPLOAD_MB", "1")
    response = client.post(
        "/v1/parse", content=b"0" * (2 * 1024 * 1024), headers={"Content-Type": "application/pdf"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


def test_hard_deadline_and_recovery(sample_pdf_path):
    response = upload(sample_pdf_path, {"timeout_seconds": 0.001})
    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "PARSE_TIMEOUT"
    assert upload(sample_pdf_path, {"ocr": "never"}).status_code == 200


def test_bad_pdf_does_not_break_next_request(sample_pdf_path):
    bad = client.post("/v1/parse", files={"file": ("bad.pdf", b"not a PDF")})
    assert bad.status_code == 400
    assert upload(sample_pdf_path, {"ocr": "never"}).status_code == 200


def test_password_errors(encrypted_pdf_path):
    assert upload(encrypted_pdf_path).status_code == 422
    assert upload(encrypted_pdf_path, {"password": "wrong"}).status_code == 403
    assert upload(encrypted_pdf_path, {"password": "s3cret", "ocr": "never"}).status_code == 200
