# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned
- Local Vision-Language Model (VLM) chart annotation support.
- Specialized formula-to-LaTeX converter plugin.
- Accuracy regression corpus with text-recall and table-F1 metrics.

---

## [0.1.2] - 2026-08-25

### Security
- **Arbitrary Write Path**: The `output_path` request field let any caller write parsing output to any directory the process could reach. The output root is now server-controlled via `TRUEPARSE_OUTPUT_ROOT`.
- **Arbitrary File Read**: `POST /v1/documents/inspect` accepted a server-side `file_path`. It now takes a file upload.
- **Path Traversal**: Removed the `output_root` query parameter from the retrieval endpoints; `document_id` and `asset_id` are sanitized and re-checked for containment.
- **Unbounded Upload Memory**: Uploads are streamed to disk with the size limit enforced mid-stream (`TRUEPARSE_MAX_UPLOAD_MB`, default 200).
- **Optional API-Key Auth**: Set `TRUEPARSE_API_KEY` to require an `X-API-Key` header. Unset by default.
- **Optional CORS**: `TRUEPARSE_CORS_ORIGINS` enables CORS for an explicit origin list. Disabled by default.

### Added
- **Local OCR** (`pip install trueparse[ocr]`): Scanned pages recognized via RapidOCR/ONNX. OCR text carries `ocr_model` provenance and per-page confidence. No-ops when the extra is absent.
- **RAG Chunking** (`--chunks`): Writes `chunks.jsonl` with heading breadcrumb, page range, bounding boxes, and element IDs per chunk. Tables are never split.
- **`trueparse chunk`**: Re-chunks an existing `document.json` without re-parsing.
- **HTML and Text Export** (`--html`, `--text`).
- **Encrypted PDF Support**: `password` option on the SDK, CLI, and API.
- **Unruled Table Detection**: The `accurate` profiles also run PyMuPDF's `text` strategy.
- **Cross-Page Table Merging**: Continuation tables are folded into their predecessor.
- **Formula Detection**: Maths-heavy blocks are tagged as `EQUATION` elements.
- **`py.typed` marker**, **`trueparse --version`**, and capability flags on `/health`.

### Fixed
- **Windows Atomic-Write Crash**: `storage/filesystem.py` called `time.sleep()` without importing `time`, so the `PermissionError` retry path raised `NameError`.
- **Fabricated Table Spans**: `row_span`/`col_span` were hardcoded to `1`. Spans are now measured from the cell grid, and the HTML export emits real `colspan`/`rowspan`.
- **Dropped Table Cells**: Span detection absorbed narrow spacer columns, discarding real cells in financial tables.
- **Vector Figures Not Deduplicated**: Vector crops were stored with an empty `sha256`, so a repeated logo was written once per page.
- **Broken Section Nesting**: A heading at the same level as the previous one became its child rather than its sibling.
- **Numbered Headings Read as Lists**: `1. Introduction` matched the bullet pattern before the heading pattern.
- **Missing `Optional` import** in `document/hierarchy.py`.

### Changed
- **Parsing Profiles Now Apply**: `profile` was accepted everywhere and read nowhere. Each profile now sets render DPI, table strategies, span detection, merging, and OCR aggressiveness.
- **Document-Wide Heading Detection**: Body font size is a character-weighted mode across the whole document, combined with numbering patterns.
- **N-Column Reading Order**: Column boundaries are discovered from whitespace gutters instead of a hardcoded page midline.
- **Paragraph Merging**: Sentences split across columns and pages are rejoined, and hyphenated line breaks repaired.
- **Quality Scores Are Measured**: `layout_confidence` and `table_score` were constants. Scores now derive from coverage, overlap, unclassified ratio, OCR confidence, and table completeness.
- **Process-Based Workers**: The queue defaults to a `ProcessPoolExecutor`; set `TRUEPARSE_WORKER_MODE=thread` to opt out.
- **Stale Job Recovery**: Jobs left mid-flight by a killed process are failed at startup. SQLite runs in WAL mode.
- **Single-Sourced Version**: Read from distribution metadata instead of four hardcoded copies.
- **Schema version raised to `1.1`.**
- **Docker**: Dropped unused Tesseract packages; OCR included by default (`--build-arg INSTALL_OCR=false` to opt out).
- **CI**: Added `ruff` and `mypy` gates and Python 3.13; test suite grown from 14 to 154 tests.

### Removed
- `output_path` form field and `output_root` query parameter (see Security).
- `InspectDocumentRequest` schema and the JSON body form of `/v1/documents/inspect`.

### Migration Notes
- Drop `output_path` / `output_root` from API calls and set `TRUEPARSE_OUTPUT_ROOT` on the server. They are ignored, not rejected.
- `/v1/documents/inspect` now takes `multipart/form-data` with a `file` field.
- `ParseOptions.render_dpi` defaults to `None` (use the profile's DPI) instead of `150`.
- `TableElement.cells` may contain fewer than `rows * columns` entries, since cells covered by a span are omitted.
- The SDK, CLI, and `document.json` shape are otherwise backwards compatible.

---

## [0.1.1] - 2026-08-25

### Fixed
- **Duplicate Logging**: Set `logger.propagate = False` and unified logger namespace under `"trueparse"` to prevent double-printed console output.
- **Cross-Platform CLI Compatibility**: Replaced non-ASCII table glyphs with universal text formatting for legacy Windows console compatibility.

### Added
- **CLI Serve Command**: Added `trueparse serve` command to start the FastAPI server and Swagger docs directly from the terminal.
- **Top-Level Package Exports**: Exposed `PDFInspector` and `DocumentInspection` at the root `trueparse` namespace for rapid forensics inspection.

---

## [0.1.0] - 2026-08-25

### Added
- **Native Document Parsing Engine**: High-speed, local-first PDF parser powered by MuPDF C-engine bindings (`pymupdf` and `pymupdf-layout`).
- **True Visual Asset Extraction**: Direct raster image stream extraction with sub-pixel bounding coordinates.
- **Cryptographic Asset Deduplication**: SHA-256 visual hashing to deduplicate logos, icons, and repeated assets across pages.
- **Structural Table Reconstruction**: Ruled and unruled table extraction with cell coordinates, row/column spans, Markdown, and HTML serialization.
- **Multi-Column Reading Order**: Bounding-box spatial geometry engine to reconstruct natural reading flow across complex multi-column layouts.
- **Section & Heading Hierarchy**: Document graph builder reconstructing heading trees (`title`, `h1`, `h2`, `h3`) and parent-child containment.
- **Asynchronous Task Queue**: Local-first background execution engine backed by SQLite (`data/jobs.db`) with real-time page-by-page progress tracking.
- **Multi-File Parallel Batch Processing**: Support for concurrent multi-document batch parsing across available CPU cores.
- **Unified Interfaces**:
  - Python SDK (`from trueparse import TrueParser, PDFParser, ParseOptions`).
  - Command-Line Interface (`trueparse parse`, `trueparse parse-async`, `trueparse batch`, `trueparse inspect`).
  - FastAPI REST microservice with interactive OpenAPI/Swagger (`/docs`) and ReDoc (`/redoc`) documentation.
  - Production Dockerfile and Docker Compose environment.
- **Quality & Provenance Scoring**: Confidence evaluation and element-level provenance tracking (`native_pdf`, `ocr`, `heuristic`).
- **Automated Test Suite**: 14 unit and integration tests covering pipeline, tables, visual extraction, SQLite queue, and REST endpoints.
