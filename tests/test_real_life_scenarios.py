"""Real document workflows through the SDK and an isolated live HTTP server.

Run: python -m pytest tests/test_real_life_scenarios.py -v --junitxml=data/scenarios.xml
The stateless live server runs on a private ephemeral port.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import httpx
import pymupdf as fitz
import pytest

from trueparse import ParseError, TrueParseClient
from trueparse import parse as parse_pdf
from trueparse.extraction.ocr.engine import ocr_available

PROJECT = Path(__file__).resolve().parents[1]
FORMATS = ["json", "markdown", "html", "text", "chunks"]
CORPUS = sorted((PROJECT / "data" / "InputPDF").glob("*.pdf"))


@pytest.fixture(scope="module")
def live_api(tmp_path_factory):
    work = tmp_path_factory.mktemp("live_scenarios")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(PROJECT / "src"),
            "TRUEPARSE_API_KEY": "scenario-test-key",
            "TRUEPARSE_MAX_UPLOAD_MB": "32",
        }
    )
    launcher = "\n".join(
        [
            "import pathlib, threading, time, uvicorn",
            f"server = uvicorn.Server(uvicorn.Config('trueparse.api.app:app', host='127.0.0.1', port={port}, log_level='warning'))",
            "def watch():",
            "    while not pathlib.Path('stop-server').exists(): time.sleep(0.1)",
            "    server.should_exit = True",
            "threading.Thread(target=watch, daemon=True).start()",
            "server.run()",
        ]
    )
    with (work / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", launcher],
            cwd=work,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=180, headers={"X-API-Key": "scenario-test-key"}
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail((work / "server.log").read_text(encoding="utf-8"))
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Live API did not become ready in 30 seconds")
            yield client, work
        finally:
            client.close()
            (work / "stop-server").touch()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


@pytest.fixture(scope="module")
def documents(
    tmp_path_factory,
    sample_pdf_path,
    scanned_pdf_path,
    encrypted_pdf_path,
    three_column_pdf_path,
    spanned_table_pdf_path,
):
    folder = tmp_path_factory.mktemp("workflow_documents")
    result = {
        "invoice": sample_pdf_path,
        "scanned": scanned_pdf_path,
        "encrypted": encrypted_pdf_path,
        "columns": three_column_pdf_path,
        "spanned": spanned_table_pdf_path,
    }
    for kind in (
        "blank",
        "contract",
        "rotated",
        "unicode",
        "long",
        "continuation",
        "repeated_image",
        "mixed",
    ):
        doc = fitz.open()
        count = 3 if kind in ("contract", "continuation", "repeated_image") else 12 if kind == "long" else 1
        if kind == "mixed":
            with fitz.open(scanned_pdf_path) as scan:
                doc.insert_pdf(scan)
            page = doc.new_page()
            page.insert_text((54, 100), "Native appendix after scanned invoice.", fontsize=12)
        for index in range(0 if kind == "mixed" else count):
            page = doc.new_page(width=612, height=792)
            if kind == "blank":
                continue
            if kind == "unicode":
                page.insert_text((54, 100), "客户发票 合同 测试", fontname="china-s", fontsize=18)
                page.insert_text((54, 160), "International invoice reference 2026.", fontsize=12)
            elif kind == "continuation":
                if index:
                    page.insert_text((54, 70), "Financial table continued", fontsize=16)
                for x in (54, 300, 550):
                    page.draw_line((x, 100), (x, 220))
                for y in (100, 140, 180, 220):
                    page.draw_line((54, y), (550, y))
                for row, (name, value) in enumerate(
                    [
                        ("Metric", "Value"),
                        (f"Quarter{index + 1}Sales", str(1000 + index)),
                        (f"Quarter{index + 1}Costs", str(500 + index)),
                    ]
                ):
                    page.insert_text((65, 125 + row * 40), name, fontsize=11)
                    page.insert_text((310, 125 + row * 40), value, fontsize=11)
            elif kind == "repeated_image":
                with fitz.open(sample_pdf_path) as original:
                    raw = original.extract_image(original[0].get_images()[0][0])["image"]
                page.insert_image(fitz.Rect(54, 150, 254, 250), stream=raw)
                page.insert_text((54, 100), f"Company logo on statement page {index + 1}.", fontsize=12)
            else:
                page.insert_text((54, 72), f"{index + 1}. Service Agreement", fontsize=18)
                for row in range(20 if kind == "long" else 3):
                    page.insert_text(
                        (54, 130 + row * 26),
                        f"Clause {index + 1}.{row + 1} requires monthly payment and written approval.",
                        fontsize=11,
                    )
            if kind == "rotated":
                page.set_rotation(90)
        doc.set_metadata({"title": f"Realistic {kind} document", "author": "Scenario QA"})
        target = folder / f"{kind}.pdf"
        doc.save(target)
        doc.close()
        result[kind] = target
    result["invalid"] = folder / "corrupt.pdf"
    result["invalid"].write_bytes(b"This is not a PDF document")
    result["empty"] = folder / "empty.pdf"
    result["empty"].write_bytes(b"")
    return result


@pytest.fixture(params=["sdk", "api"])
def mode(request):
    return request.param


def parse(mode, path, live_api, **options):
    options = {"formats": FORMATS, "ocr": "never", **options}
    if mode == "sdk":
        return parse_pdf(path, **options).model_dump(mode="json")
    response = live_api[0].post(
        "/v1/parse",
        files={"file": (path.name, path.read_bytes(), "application/pdf")},
        data={"options": json.dumps(options)},
    )
    assert response.status_code == 200, response.text[:1000]
    return response.json()


@pytest.mark.parametrize(
    "kind,term,pages",
    [
        ("invoice", "Revenue", 3),
        ("contract", "monthly payment", 3),
        ("columns", "Column 3 block 3", 1),
        ("spanned", "Revenue", 1),
        ("rotated", "written approval", 1),
        ("unicode", "客户发票", 1),
        ("long", "Clause 12.20", 12),
        ("blank", "", 1),
    ],
)
def test_business_documents(mode, kind, term, pages, documents, live_api):
    result = parse(mode, documents[kind], live_api)
    assert result["metrics"]["page_count"] == pages
    assert result["formats_provided"] == FORMATS
    for fmt in ("markdown", "html", "text"):
        assert term in result["data"][fmt]
    if kind == "columns":
        text = result["data"]["text"]
        assert (
            text.index("Column 1 block 3") < text.index("Column 2 block 1") < text.index("Column 3 block 1")
        )
    if kind == "spanned":
        tables = [e for e in result["data"]["json"]["nodes"].values() if e["type"] == "table"]
        assert any(c["col_span"] == 2 for t in tables for c in t["cells"])


@pytest.mark.parametrize("fmt", FORMATS)
def test_single_output(mode, fmt, documents, live_api):
    result = parse(mode, documents["invoice"], live_api, formats=[fmt])
    assert {name for name, value in result["data"].items() if value is not None} == {fmt}


@pytest.mark.parametrize("kind", ["scanned", "mixed"])
@pytest.mark.parametrize("ocr", ["auto", "always", "never"])
def test_local_ocr(mode, kind, ocr, documents, live_api):
    if ocr != "never" and not ocr_available():
        pytest.skip("Install trueparse[ocr] for actual recognition")
    result = parse(mode, documents[kind], live_api, ocr=ocr, json_options={"quality_scores": True})
    graph = result["data"]["json"]
    if ocr == "never":
        assert graph["diagnostics"]["document"]["ocr_pages"] == 0
    else:
        assert graph["diagnostics"]["pages"]["1"]["ocr_applied"]
        assert "INVOICE" in result["data"]["text"].upper()
    if kind == "mixed":
        assert "Native appendix" in result["data"]["text"]


@pytest.mark.parametrize("images", ["none", "base64"])
def test_visual_delivery_and_json_independence(mode, images, documents, live_api):
    result = parse(mode, documents["repeated_image"], live_api, images=images)
    assets = result["data"]["json"]["assets"]
    if images == "none":
        assert assets and all(a["availability"] == "omitted" for a in assets.values())
    else:
        assert len(assets) == 1
        asset = next(iter(assets.values()))
        assert (
            len(
                [
                    n
                    for n in result["data"]["json"]["nodes"].values()
                    if n["type"] == "visual" and n["asset_id"] == asset["id"]
                ]
            )
            == 3
        )
        assert base64.b64decode(asset["data_uri"].split(",", 1)[1])
        assert "data:image/" in result["data"]["markdown"]
        projected = parse(
            mode, documents["repeated_image"], live_api, images="base64", json_options={"text_only": True}
        )
        assert projected["data"]["json"]["assets"] == {}
        assert "data:image/" in projected["data"]["markdown"]


@pytest.mark.parametrize("profile", ["fast", "balanced", "accurate", "maximum_accuracy"])
def test_profile_and_page_limit(mode, profile, documents, live_api):
    result = parse(mode, documents["contract"], live_api, profile=profile, max_pages=1)
    assert result["metrics"]["page_count"] == 1
    assert "Clause 2." not in result["data"]["text"]


def test_multi_page_financial_table(mode, documents, live_api):
    result = parse(mode, documents["continuation"], live_api, chunk_size=32)
    tables = [e for e in result["data"]["json"]["nodes"].values() if e["type"] == "table"]
    assert len(tables) == 1
    assert tables[0]["rows"] == 7
    assert {source["page"] for source in tables[0]["source"]} == {1, 2, 3}
    assert {cell["source"]["page"] for cell in tables[0]["cells"]} == {1, 2, 3}
    header = tables[0]["markdown"].splitlines()[:2]
    chunks = [chunk for chunk in result["data"]["chunks"] if "table" in chunk["element_types"]]
    assert len(chunks) > 1
    assert all(chunk["text"].splitlines()[:2] == header for chunk in chunks)


@pytest.mark.parametrize("path", CORPUS, ids=[path.stem for path in CORPUS])
def test_supplied_reports(mode, path, live_api, record_property):
    with fitz.open(path) as doc:
        count = len(doc)
    pages = count if count <= 12 else 8
    result = parse(
        mode, path, live_api, formats=["json", "markdown", "chunks"], max_pages=pages, timeout_seconds=120
    )
    assert result["metrics"]["page_count"] == pages
    assert len(result["data"]["markdown"]) > 100
    assert result["data"]["chunks"]
    assert all(chunk["element_ids"] for chunk in result["data"]["chunks"])
    record_property("source_pages", count)
    record_property("tested_pages", pages)
    record_property("duration_ms", result["metrics"]["duration_ms"])


def test_http_client_matches_local_sdk(documents, live_api):
    options = {"formats": ["json", "markdown"], "images": "base64", "ocr": "never"}
    with TrueParseClient(str(live_api[0].base_url), api_key="scenario-test-key") as client:
        remote = client.parse(documents["invoice"], **options)
        local = parse_pdf(documents["invoice"], **options)
        assert remote.model_dump(mode="json")["data"] == local.model_dump(mode="json")["data"]
        asset_id = next(iter(remote.document["assets"]))
        assert remote.get_asset(asset_id) == local.get_asset(asset_id)
        with pytest.raises(ParseError):
            client.parse(documents["invalid"])
        assert client.parse(documents["contract"], ocr="never").markdown


def test_parallel_http_requests(documents, live_api):
    def request(_):
        return parse("api", documents["invoice"], live_api, formats=["text"])["data"]["text"]

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(request, range(3)))
    assert len(set(results)) == 1


def test_bytes_and_explicit_export(documents, tmp_path):
    result = parse_pdf(
        BytesIO(documents["invoice"].read_bytes()),
        filename="My report.pdf",
        formats=["json", "markdown"],
        images="path",
        ocr="never",
    )
    folder = result.save(tmp_path / "export")
    assert (folder / "My report.md").exists()
    with pytest.raises(FileExistsError):
        result.save(folder)
    assert "assets" in {path.name for path in folder.iterdir()}
