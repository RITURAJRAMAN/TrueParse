import os

import pytest
from fastapi.testclient import TestClient

from trueparse.api.routes import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    # 0.1.2 surfaces capability flags so clients can detect OCR and auth.
    assert "ocr_available" in data
    assert "auth_required" in data
    assert "output_root" in data


def test_inspect_endpoint_accepts_upload(sample_pdf_path):
    with open(sample_pdf_path, "rb") as f:
        response = client.post(
            "/v1/documents/inspect",
            files={"file": (sample_pdf_path.name, f, "application/pdf")},
        )
    assert response.status_code == 200
    data = response.json()
    assert "inspection" in data
    assert data["inspection"]["page_count"] > 0
    # The server-side temp path must never be echoed back to the caller.
    assert data["inspection"]["file_path"] == sample_pdf_path.name


def test_inspect_endpoint_rejects_server_side_path(sample_pdf_path):
    """The pre-0.1.2 JSON body allowed reading any file on the host."""
    response = client.post(
        "/v1/documents/inspect", json={"file_path": str(sample_pdf_path)}
    )
    assert response.status_code == 422


def test_parse_endpoint(sample_pdf_path, test_output_root):
    with open(sample_pdf_path, "rb") as f:
        response = client.post(
            "/v1/documents/parse",
            files={"file": (sample_pdf_path.name, f, "application/pdf")},
            data={"max_pages": 2},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "document_id" in data
    assert "quality_score" in data
    # Output must land under the server-controlled root, not anywhere a client asked for.
    assert str(test_output_root) in data["document_path"]


def test_parse_endpoint_ignores_client_output_path(sample_pdf_path, tmp_path, test_output_root):
    """A client-supplied output_path must not redirect where files are written."""
    attacker_dir = tmp_path / "attacker_controlled"
    with open(sample_pdf_path, "rb") as f:
        response = client.post(
            "/v1/documents/parse",
            files={"file": (sample_pdf_path.name, f, "application/pdf")},
            data={"output_path": str(attacker_dir), "max_pages": 1},
        )
    assert response.status_code == 200
    assert not attacker_dir.exists()
    assert str(test_output_root) in response.json()["document_path"]


def test_parse_endpoint_rejects_non_pdf():
    response = client.post(
        "/v1/documents/parse",
        files={"file": ("payload.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 400


def test_document_json_roundtrip(sample_pdf_path):
    with open(sample_pdf_path, "rb") as f:
        parse = client.post(
            "/v1/documents/parse",
            files={"file": (sample_pdf_path.name, f, "application/pdf")},
            data={"max_pages": 1, "emit_html": "true", "emit_chunks": "true"},
        )
    document_id = parse.json()["document_id"]

    assert client.get(f"/v1/documents/{document_id}/json").status_code == 200
    assert client.get(f"/v1/documents/{document_id}/markdown").status_code == 200
    assert client.get(f"/v1/documents/{document_id}/html").status_code == 200

    chunks = client.get(f"/v1/documents/{document_id}/chunks")
    assert chunks.status_code == 200
    assert chunks.text.strip(), "chunks.jsonl should not be empty"


@pytest.mark.parametrize(
    "document_id",
    ["../../../../etc", "..%2F..%2Fetc", "....//....//etc"],
)
def test_document_json_rejects_traversal(document_id):
    """Identifiers are sanitised, so traversal resolves to a missing document."""
    response = client.get(f"/v1/documents/{document_id}/json")
    assert response.status_code in (404, 400)


def test_asset_endpoint_rejects_traversal(sample_pdf_path):
    with open(sample_pdf_path, "rb") as f:
        parse = client.post(
            "/v1/documents/parse",
            files={"file": (sample_pdf_path.name, f, "application/pdf")},
            data={"max_pages": 1},
        )
    document_id = parse.json()["document_id"]
    response = client.get(f"/v1/documents/{document_id}/assets/..%2F..%2Fdocument")
    assert response.status_code == 404


def test_upload_size_limit_enforced(sample_pdf_path, monkeypatch):
    """Oversized uploads are rejected while streaming, not after buffering."""
    monkeypatch.setenv("TRUEPARSE_MAX_UPLOAD_MB", "1")
    oversized = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
    response = client.post(
        "/v1/documents/parse",
        files={"file": ("big.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


def test_api_key_enforced_when_configured(sample_pdf_path, monkeypatch):
    """Setting TRUEPARSE_API_KEY locks down every parsing endpoint."""
    monkeypatch.setenv("TRUEPARSE_API_KEY", "test-secret-key")

    with open(sample_pdf_path, "rb") as f:
        unauthorized = client.post(
            "/v1/documents/parse",
            files={"file": (sample_pdf_path.name, f, "application/pdf")},
            data={"max_pages": 1},
        )
    assert unauthorized.status_code == 401

    with open(sample_pdf_path, "rb") as f:
        authorized = client.post(
            "/v1/documents/parse",
            files={"file": (sample_pdf_path.name, f, "application/pdf")},
            data={"max_pages": 1},
            headers={"X-API-Key": "test-secret-key"},
        )
    assert authorized.status_code == 200


def test_api_key_absent_leaves_endpoints_open(sample_pdf_path):
    assert os.environ.get("TRUEPARSE_API_KEY") is None
    response = client.get("/health")
    assert response.json()["auth_required"] is False


def test_encrypted_pdf_requires_password(encrypted_pdf_path):
    with open(encrypted_pdf_path, "rb") as f:
        no_password = client.post(
            "/v1/documents/parse",
            files={"file": (encrypted_pdf_path.name, f, "application/pdf")},
        )
    assert no_password.status_code == 422
    assert no_password.json()["error"]["code"] == "PDF_PASSWORD_REQUIRED"

    with open(encrypted_pdf_path, "rb") as f:
        with_password = client.post(
            "/v1/documents/parse",
            files={"file": (encrypted_pdf_path.name, f, "application/pdf")},
            data={"password": "s3cret"},
        )
    assert with_password.status_code == 200
    assert with_password.json()["page_count"] == 1
