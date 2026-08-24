from pathlib import Path
from fastapi.testclient import TestClient
from trueparse.api.routes import app

client = TestClient(app)
DATA_DIR = Path(__file__).parent.parent / "Data" / "InputPDF"
TEST_PDF = DATA_DIR / "Q226+Mgt+Report.pdf"


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_inspect_endpoint():
    response = client.post("/v1/documents/inspect", json={"file_path": str(TEST_PDF)})
    assert response.status_code == 200
    data = response.json()
    assert "inspection" in data
    assert data["inspection"]["page_count"] > 0


def test_parse_endpoint(tmp_path):
    with open(TEST_PDF, "rb") as f:
        response = client.post(
            "/v1/documents/parse",
            files={"file": ("test.pdf", f, "application/pdf")},
            data={"output_path": str(tmp_path)},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "document_id" in data
    assert "quality_score" in data
