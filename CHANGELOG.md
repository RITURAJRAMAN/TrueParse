# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned
- Local Vision-Language Model (VLM) chart annotation support.
- Specialized formula-to-LaTeX converter plugin.

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
