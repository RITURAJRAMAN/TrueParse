# TrueParse

[![PyPI Version](https://img.shields.io/badge/pypi-v0.1.0-blue.svg)](https://pypi.org/project/trueparse/)
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
- **SHA-256 Deduplication**: Hashes visual assets across pages, automatically eliminating duplicate logos, icons, and repeated decorative elements.
- **Structural Table Reconstruction**: Parses ruled and unruled tables into structured 2D cell grids, row/column spans, bounding boxes, and pre-rendered Markdown/HTML tables.
- **Multi-Column Reading Order**: Uses spatial geometry and column detection to restore natural human reading sequences across magazine layouts, multi-column reports, and sidebars.
- **Document Graph & Heading Hierarchy**: Reconstructs heading trees (`title`, `h1`, `h2`, `h3`), section boundaries, parent-child containment, and caption-to-asset bindings.
- **Multi-Worker Parallel Processing**: Built-in SQLite-backed background task manager supporting non-blocking asynchronous jobs, real-time page-by-page progress tracking, and multi-file batch execution.
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

# 3. Parse with debug page rendering enabled
trueparse parse "path/to/document.pdf" --debug

# 4. Asynchronous parsing with live progress spinner
trueparse parse-async "path/to/document.pdf"

# 5. Parallel batch parsing across all PDFs in a directory
trueparse batch "path/to/pdf_folder"

# 6. Instant PDF forensics and structure inspection (no full parse)
trueparse inspect "path/to/document.pdf"
```

---

## 2. Python SDK

Import `trueparse` directly in your Python applications with top-level exports:

```python
from trueparse import PDFParser, ParseOptions, ParsingProfile

# 1. Configure parser options
options = ParseOptions(
    profile=ParsingProfile.BALANCED,
    extract_images=True,
    extract_tables=True,
    extract_charts=True,
    extract_formulas=True,
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
| `GET` | `/health` | Service health status and engine version |
| `POST` | `/v1/documents/parse` | Synchronous PDF parsing returning complete document JSON |
| `POST` | `/v1/documents/parse-async` | Non-blocking async parse submission (returns `202 Accepted` & `job_id`) |
| `GET` | `/v1/documents/jobs/{job_id}` | Live job status, real-time page-by-page progress %, and result paths |
| `POST` | `/v1/batches/parse-async` | Multi-file batch upload (up to 100 PDFs) for parallel background execution |
| `GET` | `/v1/batches/{batch_id}` | Aggregated progress metrics and document statuses for a batch |
| `GET` | `/v1/documents/{document_id}` | Retrieve stored `document.json` by document ID |
| `GET` | `/v1/documents/{document_id}/assets/{asset_id}` | Download raw extracted visual image/chart binary |

#### Example cURL Requests

**Synchronous Parse**:
```bash
curl -X POST "http://localhost:8000/v1/documents/parse" \
  -F "file=@document.pdf" \
  -F "extract_images=true" \
  -F "extract_tables=true"
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
│   └── document.md           # Derived clean Markdown export
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
  "schema_version": "1.0",
  "engine_version": "0.1.0",
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
    "text_density": 0.98,
    "layout_confidence": 0.96,
    "warnings": []
  }
}
```

---

## Parsing Profiles Matrix

TrueParse includes 4 configurable parsing profiles:

| Profile | Focus | Best For |
| :--- | :--- | :--- |
| **`fast`** | Pure native extraction & spatial ordering | Text-heavy digital PDFs, high-throughput pipelines |
| **`balanced`** *(Default)* | Native layout, table grids & conditional OCR | Standard enterprise documents, mixed layouts |
| **`accurate`** | Enhanced table reconciliation & visual clustering | Financial statements, complex reports, legal filings |
| **`maximum_accuracy`** | Full structural reconciliation & validation | Academic papers, multi-column scientific publications |

---

## Testing

Run the automated test suite with `pytest`:

```bash
# Run all tests
pytest

# Run tests with verbose output
pytest -v

# Run queue concurrency tests
pytest tests/test_queue.py
```

---

## Documentation

- [api_documentation.md](api_documentation.md) — Comprehensive API & Schema Specification
- [CHANGELOG.md](CHANGELOG.md) — Release History & Changelog

---

## License

This project is licensed under the **[MIT License](LICENSE)**.
