# TrueParse

[![PyPI Version](https://img.shields.io/pypi/v/trueparse.svg)](https://pypi.org/project/trueparse/)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker Ready](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](Dockerfile)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2.0%2B-E92063.svg)](https://docs.pydantic.dev/)

> **High-performance, local-first PDF parsing and document intelligence engine that converts arbitrary PDFs into canonical, structured JSON and clean Markdown while extracting true visual assets and reconstructed table grids.**

---

## Overview

**TrueParse** is built for engineering teams who need accurate, sub-pixel document parsing without sending sensitive data to external cloud APIs. It parses complex multi-column layouts, extracts embedded raster images directly from PDF bytecode, reconstructs table cell grids with row/column spans, builds relational section hierarchies, and tracks exact spatial coordinates for every element.

### Key Differentiators

- **True Visual Asset Extraction**: Extracts actual raster image streams directly without lossy re-compression or replacing images with whole-page screenshots.
- **SHA-256 Deduplication**: Hashes every visual asset — raster *and* vector crop — across pages, so a logo repeated on 40 pages is stored once with 40 recorded occurrences.
- **Structural Table Reconstruction**: Parses ruled and unruled tables into 2D cell grids with real row/column spans measured from the grid geometry, bounding boxes, and pre-rendered Markdown/HTML.
- **N-Column Reading Order**: Discovers column boundaries from whitespace gutters rather than assuming a page midline, so three-column journals and off-centre sidebars read in the right order.
- **Document Graph & Heading Hierarchy**: Reconstructs heading trees from a document-wide font ladder *and* numbering patterns (`3.1.2 Results`), section boundaries, parent-child containment, and caption-to-asset bindings.
- **Retrieval-Ready Chunking**: Emits `chunks.jsonl` where every chunk carries its heading breadcrumb, page range, and bounding boxes — enough provenance to cite a RAG answer back to a rectangle on a page.
- **Optional Local OCR**: Scanned pages are recognised via RapidOCR/ONNX — a pure wheel, no system packages — keeping the zero-cloud guarantee intact.
- **Multi-Worker Parallel Processing**: SQLite-backed background task manager with process-based workers, real-time page-by-page progress, and multi-file batch execution.
- **Zero Cloud Dependencies**: 100% local, privacy-preserving, and free of recurring per-page API costs.

---

## 4 Deployment & Usage Modes

TrueParse is engineered to support four distinct integration patterns:

1. [**Pip Package (CLI)**](#1-pip-package--cli) — Install directly from PyPI into your environment.
2. [**Direct Python SDK**](#2-python-sdk) — Embed directly into your application codebase.
3. [**FastAPI REST Service**](#3-fastapi-rest-service) — Self-host as a standalone microservice with Swagger documentation.
4. [**Docker Container**](#4-docker-container-deployment) — One-command local container hosting with zero Python setup.

---

## 1. Pip Package & CLI

Install TrueParse directly via `pip`:

```bash
# Install from PyPI
pip install trueparse

# With local OCR support for scanned documents
pip install "trueparse[ocr]"
# On a minimal Linux image, OCR also needs OpenCV's system libraries:
#   sudo apt-get install -y libgl1 libglib2.0-0

# Or install latest directly from GitHub
pip install git+https://github.com/RITURAJRAMAN/TrueParse.git
```

This installs the core library and registers the `trueparse` command-line utility:

### CLI Commands

```bash
# 1. Parse a PDF document (outputs to data/output/{document_id}/)
trueparse parse "path/to/document.pdf"

# 2. Parse with custom output directory and page limits
trueparse parse "path/to/document.pdf" -o "./results" --max-pages 5

# 3. Choose a parsing profile (fast | balanced | accurate | maximum_accuracy)
trueparse parse "path/to/document.pdf" --profile accurate

# 4. Emit RAG-ready chunks alongside the JSON
trueparse parse "path/to/document.pdf" --chunks --chunk-size 512 --overlap 64

# 5. Emit additional export formats
trueparse parse "path/to/document.pdf" --html --text

# 6. Force OCR on a scanned document (needs: pip install "trueparse[ocr]")
trueparse parse "path/to/scan.pdf" --ocr always

# 7. Unlock an encrypted PDF
trueparse parse "path/to/protected.pdf" --password "s3cret"

# 8. Re-chunk an already-parsed document without re-parsing the PDF
trueparse chunk "data/output/doc_abc123/output/document.json" --chunk-size 256

# 9. Asynchronous parsing with live progress spinner
trueparse parse-async "path/to/document.pdf"

# 10. Parallel batch parsing across all PDFs in a directory
trueparse batch "path/to/pdf_folder" --recursive

# 11. Instant PDF forensics and structure inspection (no full parse)
trueparse inspect "path/to/document.pdf"

# 12. Show the installed version
trueparse --version
```

Run `trueparse <command> --help` for the full flag list. Chunking flags
(`--chunks`, `--chunk-strategy`, `--chunk-size`, `--overlap`) accept
`section`, `token`, or `hybrid` for the strategy.

---

## 2. Python SDK

Import `trueparse` directly in your Python applications with top-level exports:

```python
from trueparse import PDFParser, ParseOptions, ParsingProfile, OCRMode

# 1. Configure parser options
options = ParseOptions(
    profile=ParsingProfile.BALANCED,
    extract_images=True,
    extract_tables=True,
    extract_charts=True,
    extract_formulas=True,
    ocr=OCRMode.AUTO,        # auto | always | never
    password=None,           # for encrypted PDFs
    emit_chunks=True,        # also write chunks.jsonl
    debug=False,
)

# 2. Initialize parser instance
parser = PDFParser(options=options)

# 3. Parse PDF document
doc = parser.parse("path/to/document.pdf")

# 4. Access document metadata and quality metrics
print(f"Document ID: {doc.id}")
print(f"Page Count: {len(doc.pages)}")
print(f"Unique Extracted Assets: {len(doc.assets)}")
print(f"Quality Score: {doc.quality.overall_score:.2f}")

# 5. Traverse structured sections and heading hierarchy
for section in doc.sections:
    print(f"[{section.level}] {section.title} (Pages: {section.page_range})")

# 6. Access page elements with exact coordinates
for page in doc.pages:
    print(f"\n--- Page {page.page_number} ({page.width}x{page.height} pt) ---")
    for elem in page.elements:
        bbox = f"({elem.bbox.x0:.1f}, {elem.bbox.y0:.1f}, {elem.bbox.x1:.1f}, {elem.bbox.y1:.1f})"
        print(f"[{elem.type}] {bbox}: {elem.content[:60]}")
```

### Retrieval Chunking

Chunks carry the provenance needed to cite a retrieved answer back to its exact
location in the source PDF:

```python
from trueparse import DocumentChunker, ChunkStrategy

chunks = DocumentChunker.chunk(
    doc,
    strategy=ChunkStrategy.HYBRID,   # section | token | hybrid
    max_tokens=512,
    overlap_tokens=64,
)

for chunk in chunks:
    print(f"[{chunk.id}] {' > '.join(chunk.section_path)}")
    print(f"  pages {chunk.page_start}-{chunk.page_end} | ~{chunk.token_estimate} tokens")
    print(f"  elements: {chunk.element_ids}")
    print(f"  text: {chunk.text[:80]}...")

# Serialize to newline-delimited JSON for your vector store
jsonl = DocumentChunker.to_jsonl(chunks)
```

Tables are never split across chunks, and paragraphs larger than the budget are
split on sentence boundaries.

---

## 3. FastAPI REST Service

Clone and host the TrueParse service as a high-throughput REST API with live background worker queues:

```bash
# 1. Clone repository and install dependencies
git clone https://github.com/RITURAJRAMAN/TrueParse.git
cd TrueParse

python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2. Launch FastAPI service
uvicorn trueparse.api.routes:app --host 0.0.0.0 --port 8000 --reload
```

- **Interactive Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Documentation**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### REST API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service health, engine version, and capability flags (`ocr_available`, `auth_required`, `output_root`) |
| `POST` | `/v1/documents/parse` | Synchronous PDF parsing returning complete document JSON |
| `POST` | `/v1/documents/inspect` | Fast forensics on an uploaded PDF without a full parse |
| `POST` | `/v1/documents/parse-async` | Non-blocking async parse submission (returns `202 Accepted` & `job_id`) |
| `GET` | `/v1/documents/jobs/{job_id}` | Live job status, real-time page-by-page progress %, and result paths |
| `POST` | `/v1/batches/parse-async` | Multi-file batch upload (up to 100 PDFs) for parallel background execution |
| `GET` | `/v1/batches/{batch_id}` | Aggregated progress metrics and document statuses for a batch |
| `GET` | `/v1/documents/{document_id}/json` | Retrieve stored `document.json` by document ID |
| `GET` | `/v1/documents/{document_id}/markdown` | Retrieve the Markdown export |
| `GET` | `/v1/documents/{document_id}/html` | Retrieve the HTML export (if `emit_html` was set) |
| `GET` | `/v1/documents/{document_id}/chunks` | Retrieve `chunks.jsonl` (if `emit_chunks` was set) |
| `GET` | `/v1/documents/{document_id}/assets/{asset_id}` | Download raw extracted visual image/chart binary |

### Server Configuration

The service is configured by environment variables, **not** by request parameters.
Output location in particular is server-controlled: a client cannot choose where
files are written.

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `TRUEPARSE_OUTPUT_ROOT` | `data/output` | The only directory parsing artifacts may be written to. Every resolved path is checked against it. |
| `TRUEPARSE_API_KEY` | *(unset)* | When set, every parsing endpoint requires a matching `X-API-Key` header. |
| `TRUEPARSE_CORS_ORIGINS` | *(unset)* | Comma-separated allowed origins. CORS is disabled when unset. |
| `TRUEPARSE_MAX_UPLOAD_MB` | `200` | Upload ceiling, enforced while streaming to disk. |
| `TRUEPARSE_WORKER_MODE` | `process` | Background worker mode: `process` or `thread`. |
| `TRUEPARSE_MAX_WORKERS` | *(cpu count, capped at 8)* | Background worker pool size. |

> **Exposing the service beyond localhost?** Set `TRUEPARSE_API_KEY`. The API is
> unauthenticated by default so that local use needs no configuration.

```bash
export TRUEPARSE_API_KEY="your-secret-key"
export TRUEPARSE_OUTPUT_ROOT="/var/lib/trueparse/output"
trueparse serve --host 0.0.0.0 --port 8000

curl -X POST "http://localhost:8000/v1/documents/parse" \
  -H "X-API-Key: your-secret-key" \
  -F "file=@document.pdf"
```

#### Example cURL Requests

**Synchronous Parse**:
```bash
curl -X POST "http://localhost:8000/v1/documents/parse" \
  -F "file=@document.pdf" \
  -F "extract_images=true" \
  -F "extract_tables=true" \
  -F "emit_chunks=true" \
  -F "profile=accurate"
```

**Forensic Inspection (upload, no full parse)**:
```bash
curl -X POST "http://localhost:8000/v1/documents/inspect" \
  -F "file=@document.pdf"
```

**Asynchronous Background Parse & Progress Polling**:
```bash
# Submit document (returns job_id immediately)
curl -X POST "http://localhost:8000/v1/documents/parse-async" \
  -F "file=@large_document.pdf"

# Poll live progress
curl "http://localhost:8000/v1/documents/jobs/{job_id}"
```

**Parallel Multi-File Batch Parse**:
```bash
curl -X POST "http://localhost:8000/v1/batches/parse-async" \
  -F "files=@document_1.pdf" \
  -F "files=@document_2.pdf" \
  -F "files=@document_3.pdf"

# Check batch aggregation
curl "http://localhost:8000/v1/batches/{batch_id}"
```

---

## 4. Docker Container Deployment

Host TrueParse locally with Docker without needing Python or local environment setup:

### Pull Pre-Built Image (GitHub Container Registry)

```bash
# 1. Pull the official pre-built multi-platform container image
docker pull ghcr.io/riturajraman/trueparse:latest

# 2. Run with mounted local output volume
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data/output:/app/data/output \
  --name trueparse-api \
  ghcr.io/riturajraman/trueparse:latest
```

The published image includes local OCR support (~1.35 GB). To build a slimmer
image without it (~263 MB), at the cost of scanned-document support:

```bash
docker build --build-arg INSTALL_OCR=false -t trueparse-slim .
```

Check which variant you are running with `GET /health` — it reports
`"ocr_available": true|false`.

Set server configuration with `-e`, for example:

```bash
docker run -d -p 8000:8000 \
  -e TRUEPARSE_API_KEY="your-secret-key" \
  -v $(pwd)/data/output:/app/data/output \
  ghcr.io/riturajraman/trueparse:latest
```

### Using Docker Compose (Build Locally)

```bash
# Start container with persistent data volume mounts
docker compose up -d --build
```

The service is available immediately at `http://localhost:8000/docs`. Parsed outputs will automatically be saved to `./data/output/` on your host machine.

### Using Docker CLI (Build Locally)

```bash
# 1. Build Docker image
docker build -t trueparse .

# 2. Run container with mounted output volume
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data/output:/app/data/output \
  --name trueparse-service \
  trueparse
```

---

## Standardized Workspace Output

Every parsed document generates a structured, self-contained workspace under `data/output/{document_id}/`:

```text
data/output/{document_id}/
├── source/
│   └── {filename}.pdf        # Archived copy of the source PDF
├── assets/
│   ├── images/               # Deduplicated raster images (asset_img_0001.jpeg)
│   ├── figures/              # Isolated vector drawings & figures (asset_fig_0001.png)
│   ├── charts/               # Detected chart assets (asset_chart_0001.png)
│   ├── diagrams/             # Diagram assets (asset_diagram_0001.png)
│   └── formulas/             # Formula assets (asset_formula_0001.png)
├── output/
│   ├── document.json         # Canonical structured document graph
│   ├── document.md           # Derived clean Markdown export
│   ├── document.html         # Self-contained HTML export (--html)
│   ├── document.txt          # Plain reading-order text (--text)
│   └── chunks.jsonl          # Retrieval-ready chunks (--chunks)
├── intermediate/             # Intermediate processing artifacts
└── debug/
    └── pages/                # Page render debug artifacts (optional)
```

---

## Canonical JSON Schema Structure

The output `document.json` conforms to a strict, typed schema:

```json
{
  "id": "doc_f76e021893a5",
  "schema_version": "1.1",
  "engine_version": "0.1.2",
  "source_file": "annual_report.pdf",
  "metadata": {
    "title": "Annual Financial Report",
    "page_count": 106,
    "sha256": "f76e021893a5..."
  },
  "pages": [
    {
      "page_number": 1,
      "width": 612.0,
      "height": 792.0,
      "elements": [
        {
          "id": "elem_p0001_0001",
          "type": "heading_1",
          "page": 1,
          "bbox": { "x0": 54.0, "y0": 72.0, "x1": 450.0, "y1": 96.0 },
          "reading_order": 1,
          "content": "Executive Summary",
          "confidence": 0.99,
          "provenance": {
            "method": "native_pdf",
            "engine": "pymupdf",
            "confidence": 0.99
          }
        }
      ],
      "asset_ids": ["asset_img_0001"]
    }
  ],
  "sections": [
    {
      "id": "sec_0001",
      "title": "Executive Summary",
      "level": 1,
      "page_range": [1, 3],
      "element_ids": ["elem_p0001_0001", "elem_p0001_0002"],
      "subsections": []
    }
  ],
  "assets": {
    "asset_img_0001": {
      "id": "asset_img_0001",
      "type": "image",
      "relative_path": "assets/images/asset_img_0001.jpeg",
      "format": "jpeg",
      "width": 800,
      "height": 600,
      "sha256": "3a7b1c...",
      "occurrences": [
        { "page": 1, "bbox": { "x0": 54.0, "y0": 120.0, "x1": 558.0, "y1": 340.0 } }
      ]
    }
  },
  "relationships": [
    {
      "source_id": "elem_p0001_0003",
      "target_id": "asset_img_0001",
      "type": "caption_for"
    }
  ],
  "quality": {
    "overall_score": 0.97,
    "text_score": 0.99,
    "layout_score": 0.96,
    "table_score": 0.94,
    "coverage_score": 0.41,
    "ocr_pages": 0,
    "warnings": []
  }
}
```

### Chunk Records (`chunks.jsonl`)

```json
{
  "id": "chunk_00007",
  "document_id": "doc_f76e021893a5",
  "chunk_index": 7,
  "text": "Revenue for the period grew 12% year over year...",
  "token_estimate": 384,
  "section_id": "sec_0003",
  "section_path": ["Financial Review", "Revenue"],
  "page_start": 12,
  "page_end": 13,
  "bboxes": [{ "page": 12, "bbox": [54.0, 220.0, 558.0, 410.0] }],
  "element_ids": ["elem_p0012_0004", "elem_p0013_0001"],
  "element_types": ["paragraph"],
  "asset_ids": []
}
```

---

## Parsing Profiles Matrix

TrueParse includes 4 configurable parsing profiles:

Profiles are not labels — each resolves to concrete engine tuning applied on every page.

| Profile | DPI | Table Strategies | Spans | Paragraph Merge | OCR | Best For |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`fast`** | 96 | `lines` | No | No | Never | Text-heavy digital PDFs, high-throughput pipelines |
| **`balanced`** *(Default)* | 150 | `lines` | Yes | Yes | Auto | Standard enterprise documents, mixed layouts |
| **`accurate`** | 200 | `lines` + `text` | Yes | Yes | Auto | Financial statements, unruled tables, legal filings |
| **`maximum_accuracy`** | 300 | `lines` + `text` | Yes | Yes | Auto (aggressive) | Academic papers, multi-column scientific publications |

`OCR: Auto` means only pages detected as scanned are recognised, and only when the
`ocr` extra is installed. Set `--ocr always` to force it, or `--ocr never` to disable.
`render_dpi` overrides the profile's DPI when set explicitly.

---

## Testing

Run the automated test suite with `pytest`:

```bash
# Run all tests (149 tests, fully self-contained - fixtures are generated at runtime)
pytest

# Run a specific area
pytest tests/test_security.py     # path containment, auth, upload limits
pytest tests/test_chunking.py     # RAG chunking
pytest tests/test_layout.py       # reading order, headings, paragraph merging
pytest tests/test_tables.py       # span reconstruction, cross-page merging
pytest tests/test_queue.py        # SQLite queue and worker pool

# OCR tests skip automatically unless the extra is installed
pip install "trueparse[ocr]" && pytest tests/test_ocr.py

# Lint and type-check (both gated in CI)
ruff check src tests
mypy
```

---

## Documentation

- [api_documentation.md](api_documentation.md) — Comprehensive API & Schema Specification
- [CHANGELOG.md](CHANGELOG.md) — Release History & Changelog

---

## License

This project is licensed under the **[MIT License](LICENSE)**.
