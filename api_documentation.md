# TrueParse — API Specification

## 1. API version

Current:

```text
/v1
```

Base URL example:

```text
http://localhost:8000
```

## 2. Design principles

- Local-first.
- No paid external APIs.
- JSON responses.
- Large binary assets are stored separately.
- API returns references to assets rather than embedding binary content.
- Long-running parsing should eventually support asynchronous jobs.
- API schema is versioned.

## 3. Health

### GET `/health`

Response:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

## 4. Parse document

### POST `/v1/documents/parse`

Accept either multipart upload or a local-path request in trusted local deployments.

### Multipart request

```text
Content-Type: multipart/form-data
```

Fields:

```text
file: PDF file (required)
output_path: optional string (default: "data/output")
profile: optional string (default: "balanced")
debug: optional boolean (default: false)
extract_images: optional boolean (default: true)
extract_tables: optional boolean (default: true)
extract_charts: optional boolean (default: true)
extract_formulas: optional boolean (default: true)
ocr: optional string (default: "auto")
max_pages: optional integer (default: null / 0 for all pages)
```

Recommended defaults:

```json
{
  "profile": "balanced",
  "debug": false,
  "extract_images": true,
  "extract_tables": true,
  "extract_charts": true,
  "extract_formulas": true,
  "ocr": "auto",
  "max_pages": null
}
```

## 5. Profiles

### `fast`

Prefer:

- native extraction
- basic layout
- minimal ML

### `balanced`

Prefer:

- native extraction
- layout model
- table recognition
- OCR when required

### `accurate`

Enable:

- stronger layout
- table structure
- OCR fallback
- chart/figure analysis

### `maximum_accuracy`

Enable:

- native extraction
- layout ensemble where useful
- table reconciliation
- OCR fallback
- chart/figure processing
- local VLM fallback
- quality validation

The exact model configuration must be versioned.

## 6. Synchronous response

For small documents:

```json
{
  "request_id": "req_01",
  "document_id": "doc_01",
  "status": "completed",
  "schema_version": "1.0",
  "document_path": "data/output/doc_01/output/document.json",
  "asset_root": "data/output/doc_01/assets",
  "page_count": 12,
  "assets_count": 46,
  "quality_score": 0.97,
  "warnings": []
}
```

## 7. Asynchronous & Batch Processing

### POST `/v1/documents/parse-async`

Enqueues a single PDF document for non-blocking background parsing across worker threads.

#### Multipart Request Fields:
- `file`: PDF file (`required`)
- `output_path`: string (default: `"data/output"`)
- `profile`: string (`fast`, `balanced`, `accurate`, `maximum_accuracy`)
- `extract_images`: boolean (default: `true`)
- `extract_tables`: boolean (default: `true`)
- `extract_charts`: boolean (default: `true`)
- `extract_formulas`: boolean (default: `true`)
- `ocr`: string (`auto`, `always`, `never`)
- `debug`: boolean (default: `false`)
- `max_pages`: integer (default: `null` / `0` for all pages)

Response (`202 Accepted`):

```json
{
  "job_id": "job_330744d61000",
  "status": "queued",
  "source_file": "report.pdf",
  "status_url": "/v1/documents/jobs/job_330744d61000",
  "message": "Document parsing enqueued successfully"
}
```

### GET `/v1/documents/jobs/{job_id}`

Retrieves live progress and results for an asynchronous parsing job.

Response (`200 OK`):

```json
{
  "job_id": "job_330744d61000",
  "batch_id": null,
  "source_file": "report.pdf",
  "status": "completed",
  "progress": {
    "current_page": 12,
    "total_pages": 12,
    "percent": 100.0,
    "stage": "completed"
  },
  "result": {
    "document_id": "doc_f76e021893a5",
    "page_count": 12,
    "assets_count": 46,
    "sections_count": 28,
    "quality_score": 0.97,
    "document_path": "data/output/doc_f76e021893a5/output/document.json",
    "markdown_path": "data/output/doc_f76e021893a5/output/document.md",
    "asset_root": "data/output/doc_f76e021893a5/assets",
    "warnings": []
  },
  "error": null,
  "created_at": 1771542100.12,
  "updated_at": 1771542105.45
}
```

### POST `/v1/batches/parse-async`

Submits multiple PDF files for parallel background parsing across concurrent worker threads.

#### Request Constraints & Fields:
- `files`: List of PDF files (`required`, maximum **100 files** per request)
- `output_path`: string (default: `"data/output"`)
- `profile`: string (default: `"balanced"`)
- `extract_images`: boolean (default: `true`)
- `extract_tables`: boolean (default: `true`)
- `extract_charts`: boolean (default: `true`)
- `extract_formulas`: boolean (default: `true`)
- `ocr`: string (default: `"auto"`)
- `debug`: boolean (default: `false`)
- `max_pages`: integer (default: `null` / `0` for all pages)

Response (`202 Accepted`):

```json
{
  "batch_id": "batch_db991233ecd1",
  "total_documents": 2,
  "completed_count": 0,
  "failed_count": 0,
  "processing_count": 0,
  "queued_count": 2,
  "status": "queued",
  "jobs": [
    {
      "job_id": "job_111",
      "batch_id": "batch_db991233ecd1",
      "source_file": "doc1.pdf",
      "status": "queued",
      "progress": {
        "current_page": 0,
        "total_pages": 0,
        "percent": 0.0,
        "stage": "queued"
      },
      "result": null,
      "error": null,
      "created_at": 1771542100.0,
      "updated_at": 1771542100.0
    }
  ]
}
```

### GET `/v1/batches/{batch_id}`

Retrieves aggregated status and per-document progress across all items in a batch.

Response (`200 OK`):

```json
{
  "batch_id": "batch_db991233ecd1",
  "total_documents": 2,
  "completed_count": 2,
  "failed_count": 0,
  "processing_count": 0,
  "queued_count": 0,
  "status": "completed",
  "jobs": [...]
}
```

## 8. Get document result

### GET `/v1/documents/{document_id}`

Response:

```json
{
  "document_id": "doc_01",
  "status": "completed",
  "document_path": "/output/doc_01/output/document.json",
  "asset_root": "/output/doc_01/assets/"
}
```

## 9. Get document JSON

### GET `/v1/documents/{document_id}/json`

Returns the canonical document JSON.

## 10. Asset endpoint

### GET `/v1/documents/{document_id}/assets/{asset_id}`

Returns the actual asset.

Supported assets:

- image
- figure
- chart
- diagram
- formula

The API should stream the binary rather than embed it in JSON.

## 11. Debug endpoint

### GET `/v1/documents/{document_id}/debug/pages/{page_number}`

Available only when debug output is enabled.

Returns a page render or annotated debug image.

This endpoint must not be used as the canonical asset endpoint.

## 12. Inspect endpoint

### POST `/v1/documents/inspect`

Runs PDF forensics without full document parsing.

Response:

```json
{
  "page_count": 10,
  "metadata": {},
  "pages": [
    {
      "page_number": 1,
      "width": 612,
      "height": 792,
      "native_text": true,
      "embedded_images": 2,
      "drawing_count": 182,
      "likely_scan": false
    }
  ]
}
```

## 13. Request options schema

Conceptual Pydantic model:

```python
class ParseOptions(BaseModel):
    profile: Literal[
        "fast",
        "balanced",
        "accurate",
        "maximum_accuracy",
    ] = "balanced"

    debug: bool = False

    extract_images: bool = True
    extract_tables: bool = True
    extract_charts: bool = True
    extract_formulas: bool = True

    ocr: Literal["auto", "always", "never"] = "auto"

    output_path: str | None = None
```

## 14. Canonical asset response

```json
{
  "id": "asset_001",
  "type": "image",
  "path": "assets/images/asset_001.png",
  "mime_type": "image/png",
  "sha256": "...",
  "width": 800,
  "height": 600,
  "occurrences": [
    {
      "page": 3,
      "bbox": [100, 200, 500, 600]
    }
  ],
  "source": {
    "method": "embedded_pdf_image",
    "engine": "pymupdf"
  }
}
```

## 15. Canonical table response

```json
{
  "id": "table_001",
  "type": "table",
  "page": 4,
  "bbox": [72, 220, 540, 600],
  "rows": 5,
  "columns": 4,
  "cells": [
    {
      "id": "cell_001",
      "row": 0,
      "column": 0,
      "row_span": 1,
      "col_span": 1,
      "is_header": true,
      "text": "Year",
      "bbox": [72, 220, 180, 250],
      "confidence": 0.99
    }
  ],
  "source": {
    "method": "table_structure_model",
    "engine": "..."
  }
}
```

## 16. Canonical chart response

```json
{
  "id": "chart_001",
  "type": "chart",
  "page": 7,
  "bbox": [100, 250, 520, 600],
  "asset": {
    "path": "assets/charts/chart_001.png",
    "mime_type": "image/png"
  },
  "chart_type": "bar",
  "title": "Revenue",
  "axes": {
    "x": {
      "label": "Year",
      "values": ["2023", "2024", "2025"]
    },
    "y": {
      "label": "Revenue",
      "unit": "USD"
    }
  },
  "series": [],
  "extracted_data_confidence": 0.87,
  "source": {
    "method": "vector_plus_ocr",
    "confidence": 0.91
  }
}
```

## 17. Error schema

All errors should use:

```json
{
  "error": {
    "code": "PDF_PARSE_ERROR",
    "message": "Unable to parse page 7.",
    "request_id": "req_01",
    "document_id": "doc_01",
    "page": 7,
    "retryable": false
  }
}
```

Example error codes:

```text
INVALID_PDF
PDF_PARSE_ERROR
PDF_ENCRYPTED
PDF_RESOURCE_LIMIT
PAGE_RENDER_ERROR
OCR_ERROR
LAYOUT_ERROR
TABLE_EXTRACTION_ERROR
CHART_EXTRACTION_ERROR
ASSET_STORAGE_ERROR
SERIALIZATION_ERROR
```

## 18. CLI mapping

The API maps directly to the `trueparse` command-line utility:

| CLI Command | Equivalent REST Endpoint | Description |
| :--- | :--- | :--- |
| `trueparse parse <file> --output ./out` | `POST /v1/documents/parse` | Synchronous document parse |
| `trueparse parse-async <file>` | `POST /v1/documents/parse-async` | Asynchronous parse with live progress spinner |
| `trueparse batch <folder>` | `POST /v1/batches/parse-async` | Concurrent multi-file batch execution |
| `trueparse inspect <file>` | `POST /v1/documents/inspect` | Instant PDF structural forensics |

## 19. Compatibility

API version changes are required for breaking changes.

Canonical document schema should have its own:

```text
schema_version
```

which is independent from:

```text
api_version
```

## 20. Future endpoints

Do not implement initially unless required:

```text
GET /v1/documents/{id}/pages/{page}
GET /v1/documents/{id}/elements/{element_id}
POST /v1/documents/{id}/reprocess
POST /v1/documents/{id}/validate
POST /v1/benchmarks/run
GET /v1/benchmarks/{id}
```

These can be added after the core parser is stable.
