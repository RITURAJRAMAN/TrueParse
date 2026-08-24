from fastapi.testclient import TestClient
from trueparse.api.routes import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_inspect_endpoint(sample_pdf_path):
    response = client.post("/v1/documents/inspect", json={"file_path": str(sample_pdf_path)})
    assert response.status_code == 200
    data = response.json()
    assert "inspection" in data
    assert data["inspection"]["page_count"] > 0


def test_parse_endpoint(tmp_path, sample_pdf_path):
    with open(sample_pdf_path, "rb") as f:
        response = client.post(
            "/v1/documents/parse",
            files={"file": (sample_pdf_path.name, f, "application/pdf")},
            data={"output_path": str(tmp_path)},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "document_id" in data
    assert "quality_score" in data
