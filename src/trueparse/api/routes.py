from __future__ import annotations

import hmac
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from trueparse.api.schemas import (
    AsyncParseResponse,
    BatchJobResponse,
    HealthResponse,
    InspectDocumentResponse,
    JobProgressInfo,
    JobStatusResponse,
    ParseDocumentResponse,
)
from trueparse.core.config import EngineConfig, ParseOptions
from trueparse.core.enums import ChunkStrategy, ErrorCode, OCRMode, ParsingProfile
from trueparse.core.errors import PDFEngineError
from trueparse.core.logging import setup_logging
from trueparse.core.security import (
    api_key,
    contain_path,
    cors_origins,
    max_upload_bytes,
    resolved_output_root,
    sanitize_identifier,
)
from trueparse.pdf.inspector import PDFInspector
from trueparse.pipeline.runner import PDFParser
from trueparse.workers.manager import JobManager

setup_logging()
logger = logging.getLogger("trueparse")

config = EngineConfig()

#: Upload ceiling when TRUEPARSE_MAX_UPLOAD_MB is unset.
_DEFAULT_MAX_UPLOAD_MB = ParseOptions.model_fields["max_file_size_mb"].default

#: Streaming read size; an oversize upload never materialises in memory.
_UPLOAD_CHUNK_BYTES = 1024 * 1024

app = FastAPI(
    title="TrueParse API",
    version=config.engine_version,
    description=(
        "Local-first canonical PDF parsing and document understanding REST service.\n\n"
        "**Output location is server-controlled.** Set `TRUEPARSE_OUTPUT_ROOT` to choose "
        "the directory results are written to; requests cannot select it.\n\n"
        "**Optional auth.** Set `TRUEPARSE_API_KEY` to require an `X-API-Key` header "
        "on every parsing endpoint."
    ),
)

_CORS_ORIGINS = cors_origins()
if _CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    logger.info(f"CORS enabled for origins: {', '.join(_CORS_ORIGINS)}")


@app.exception_handler(PDFEngineError)
async def _engine_error_handler(request: Request, exc: PDFEngineError) -> JSONResponse:
    """Maps engine errors onto HTTP status codes without leaking stack traces."""
    status_map = {
        "INVALID_PDF": 400,
        "PDF_PARSE_ERROR": 422,
        "PDF_ENCRYPTED": 422,
        "PDF_PASSWORD_REQUIRED": 422,
        "PDF_PASSWORD_INCORRECT": 403,
        "PDF_RESOURCE_LIMIT": 413,
        "UPLOAD_TOO_LARGE": 413,
        "PATH_NOT_ALLOWED": 403,
    }
    return JSONResponse(
        status_code=status_map.get(exc.code.value, 500),
        content=exc.to_dict(),
    )


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforces ``X-API-Key`` when ``TRUEPARSE_API_KEY`` is configured.

    Auth is opt-in so that the documented local workflow (``trueparse serve``
    on localhost) needs no configuration, while an exposed deployment can be
    locked down with one environment variable.
    """
    expected = api_key()
    if expected is None:
        return
    if not x_api_key or not _constant_time_eq(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header.")


def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def _save_upload(file: UploadFile, destination: Path) -> int:
    """Streams an upload to disk, enforcing the configured size ceiling.

    Reading the whole body into memory first (the pre-0.1.2 behaviour) let a
    single large upload — or one batch of them — exhaust the process heap.

    Returns:
        Bytes written.

    Raises:
        PDFEngineError: if the upload exceeds ``TRUEPARSE_MAX_UPLOAD_MB``.
    """
    limit = max_upload_bytes(default_mb=_DEFAULT_MAX_UPLOAD_MB)
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(destination, "wb") as sink:
            while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > limit:
                    raise PDFEngineError(
                        code=ErrorCode.UPLOAD_TOO_LARGE,
                        message=(
                            f"Upload exceeds the {limit // (1024 * 1024)} MB limit. "
                            "Raise TRUEPARSE_MAX_UPLOAD_MB to allow larger files."
                        ),
                    )
                sink.write(chunk)
    except PDFEngineError:
        destination.unlink(missing_ok=True)
        raise
    return written


def _reject_non_pdf(filename: str | None) -> None:
    if not filename or not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")


def _build_options(
    profile: ParsingProfile,
    debug: bool,
    extract_images: bool,
    extract_tables: bool,
    extract_charts: bool,
    extract_formulas: bool,
    ocr: OCRMode,
    password: str | None,
    max_pages: int | None,
    emit_chunks: bool = False,
    chunk_strategy: ChunkStrategy = ChunkStrategy.HYBRID,
    chunk_max_tokens: int = 512,
    chunk_overlap_tokens: int = 64,
    emit_html: bool = False,
    emit_text: bool = False,
) -> ParseOptions:
    """Assembles ParseOptions with the server-controlled output root."""
    return ParseOptions(
        profile=profile,
        debug=debug,
        extract_images=extract_images,
        extract_tables=extract_tables,
        extract_charts=extract_charts,
        extract_formulas=extract_formulas,
        ocr=ocr,
        password=password or None,
        output_path=str(resolved_output_root()),
        max_pages=max_pages if max_pages and max_pages > 0 else None,
        emit_chunks=emit_chunks,
        chunk_strategy=chunk_strategy,
        chunk_max_tokens=chunk_max_tokens,
        chunk_overlap_tokens=chunk_overlap_tokens,
        emit_html=emit_html,
        emit_text=emit_text,
    )


@app.get("/health", response_model=HealthResponse)
def health_check():
    from trueparse.ocr.engine import ocr_available

    return HealthResponse(
        status="ok",
        version=config.engine_version,
        ocr_available=ocr_available(),
        auth_required=api_key() is not None,
        output_root=str(resolved_output_root()),
    )


@app.post(
    "/v1/documents/parse",
    response_model=ParseDocumentResponse,
    dependencies=[Depends(require_api_key)],
)
async def parse_document(
    file: UploadFile = File(..., description="PDF file to parse"),
    profile: ParsingProfile = Form(
        default=ParsingProfile.BALANCED,
        description="Parsing profile: fast, balanced, accurate, maximum_accuracy",
    ),
    debug: bool = Form(default=False, description="Enable debug page rendering"),
    extract_images: bool = Form(default=True, description="Extract embedded visual raster images"),
    extract_tables: bool = Form(default=True, description="Detect and structurally reconstruct tables"),
    extract_charts: bool = Form(default=True, description="Detect and extract charts/diagrams"),
    extract_formulas: bool = Form(default=True, description="Detect and tag equations/formulas"),
    ocr: OCRMode = Form(
        default=OCRMode.AUTO,
        description="OCR mode: auto, always, never. Requires the 'ocr' extra to be installed.",
    ),
    password: str | None = Form(default=None, description="Password for an encrypted PDF"),
    max_pages: int | None = Form(
        default=None,
        description="Limit maximum number of pages to process. Leave empty, null, or 0 for ALL pages.",
    ),
    emit_chunks: bool = Form(default=False, description="Also write chunks.jsonl for RAG ingestion"),
    chunk_strategy: ChunkStrategy = Form(
        default=ChunkStrategy.HYBRID, description="Chunking strategy: section, token, hybrid"
    ),
    chunk_max_tokens: int = Form(default=512, description="Approximate token ceiling per chunk"),
    chunk_overlap_tokens: int = Form(default=64, description="Approximate overlap tokens per chunk"),
    emit_html: bool = Form(default=False, description="Also write a standalone document.html"),
    emit_text: bool = Form(default=False, description="Also write a plain document.txt"),
):
    req_id = f"req_{uuid.uuid4().hex[:8]}"
    _reject_non_pdf(file.filename)

    tmp_dir = Path(tempfile.mkdtemp(prefix="trueparse_"))
    tmp_path = tmp_dir / "upload.pdf"

    try:
        await _save_upload(file, tmp_path)

        options = _build_options(
            profile=profile,
            debug=debug,
            extract_images=extract_images,
            extract_tables=extract_tables,
            extract_charts=extract_charts,
            extract_formulas=extract_formulas,
            ocr=ocr,
            password=password,
            max_pages=max_pages,
            emit_chunks=emit_chunks,
            chunk_strategy=chunk_strategy,
            chunk_max_tokens=chunk_max_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            emit_html=emit_html,
            emit_text=emit_text,
        )
        parser = PDFParser(options=options, config=config)
        doc = parser.parse(tmp_path, original_filename=file.filename)

        doc_dir = parser.storage.get_document_dir(doc.id)
        return ParseDocumentResponse(
            request_id=req_id,
            document_id=doc.id,
            status="completed",
            schema_version=doc.schema_version,
            document_path=str(doc_dir / "output" / "document.json"),
            asset_root=str(doc_dir / "assets"),
            page_count=len(doc.pages),
            assets_count=len(doc.assets),
            quality_score=doc.quality.overall_score,
            warnings=doc.warnings,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post(
    "/v1/documents/inspect",
    response_model=InspectDocumentResponse,
    dependencies=[Depends(require_api_key)],
)
async def inspect_document(
    file: UploadFile = File(..., description="PDF file to inspect"),
    password: str | None = Form(default=None, description="Password for an encrypted PDF"),
):
    """Fast forensic inspection of an uploaded PDF, without a full parse.

    This endpoint takes an upload rather than a server-side path. Accepting a
    path let any caller read arbitrary files from the host filesystem.
    """
    _reject_non_pdf(file.filename)
    tmp_dir = Path(tempfile.mkdtemp(prefix="trueparse_inspect_"))
    tmp_path = tmp_dir / "upload.pdf"
    try:
        await _save_upload(file, tmp_path)
        inspection = PDFInspector.inspect(tmp_path, password=password or None)
        # The temp path is an implementation detail; report the client's name.
        inspection.file_path = file.filename or "upload.pdf"
        return InspectDocumentResponse(inspection=inspection)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _document_output_file(document_id: str, filename: str) -> Path:
    """Resolves an output file for a document, refusing anything outside the root."""
    root = resolved_output_root()
    clean_id = sanitize_identifier(document_id)
    doc_dir = contain_path(clean_id, root)
    target = contain_path(Path(filename).name, doc_dir / "output")
    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{filename} not found for document {clean_id}.",
        )
    return target


@app.get("/v1/documents/{document_id}/json")
def get_document_json(document_id: str):
    return FileResponse(
        _document_output_file(document_id, "document.json"),
        media_type="application/json",
    )


@app.get("/v1/documents/{document_id}/markdown")
def get_document_markdown(document_id: str):
    return FileResponse(
        _document_output_file(document_id, "document.md"),
        media_type="text/markdown",
    )


@app.get("/v1/documents/{document_id}/html")
def get_document_html(document_id: str):
    return FileResponse(
        _document_output_file(document_id, "document.html"),
        media_type="text/html",
    )


@app.get("/v1/documents/{document_id}/chunks")
def get_document_chunks(document_id: str):
    """Returns chunks.jsonl, present only when the parse requested chunking."""
    return FileResponse(
        _document_output_file(document_id, "chunks.jsonl"),
        media_type="application/x-ndjson",
    )


@app.get("/v1/documents/{document_id}/assets/{asset_id}")
def get_document_asset(document_id: str, asset_id: str):
    root = resolved_output_root()
    clean_doc = sanitize_identifier(document_id)
    clean_asset = sanitize_identifier(asset_id)
    assets_dir = contain_path(clean_doc, root) / "assets"

    for sub in ("images", "figures", "charts", "diagrams", "formulas"):
        target_sub = assets_dir / sub
        if not target_sub.is_dir():
            continue
        for candidate in target_sub.iterdir():
            if candidate.is_file() and candidate.stem == clean_asset:
                # Re-check containment: a symlink inside the assets tree could
                # otherwise point anywhere on disk.
                return FileResponse(contain_path(candidate, root))

    raise HTTPException(
        status_code=404,
        detail=f"Asset {clean_asset} not found for document {clean_doc}.",
    )


def _intake_dir() -> Path:
    """Staging directory for uploads awaiting background processing."""
    target = resolved_output_root().parent / "intake"
    target.mkdir(parents=True, exist_ok=True)
    return target


@app.post(
    "/v1/documents/parse-async",
    response_model=AsyncParseResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def parse_document_async(
    file: UploadFile = File(..., description="PDF file to parse asynchronously"),
    profile: ParsingProfile = Form(default=ParsingProfile.BALANCED, description="Parsing profile"),
    extract_images: bool = Form(default=True, description="Extract and save embedded raster images"),
    extract_tables: bool = Form(default=True, description="Detect and extract tabular structures"),
    extract_charts: bool = Form(default=True, description="Detect and isolate vector drawings/charts"),
    extract_formulas: bool = Form(default=True, description="Detect formulas/equations"),
    ocr: OCRMode = Form(default=OCRMode.AUTO, description="OCR mode: auto, always, never"),
    password: str | None = Form(default=None, description="Password for an encrypted PDF"),
    debug: bool = Form(default=False, description="Enable debug page rendering"),
    max_pages: int | None = Form(default=None, description="Page limit; empty or 0 means all"),
    emit_chunks: bool = Form(default=False, description="Also write chunks.jsonl for RAG ingestion"),
    chunk_strategy: ChunkStrategy = Form(default=ChunkStrategy.HYBRID, description="Chunking strategy"),
    chunk_max_tokens: int = Form(default=512, description="Approximate token ceiling per chunk"),
    chunk_overlap_tokens: int = Form(default=64, description="Approximate overlap tokens per chunk"),
    emit_html: bool = Form(default=False, description="Also write a standalone document.html"),
    emit_text: bool = Form(default=False, description="Also write a plain document.txt"),
):
    """Submits a PDF for non-blocking asynchronous processing, returning a Job ID."""
    _reject_non_pdf(file.filename)

    temp_id = uuid.uuid4().hex[:10]
    saved_path = _intake_dir() / f"{temp_id}_{Path(file.filename or 'upload.pdf').name}"
    await _save_upload(file, saved_path)

    options = _build_options(
        profile=profile,
        debug=debug,
        extract_images=extract_images,
        extract_tables=extract_tables,
        extract_charts=extract_charts,
        extract_formulas=extract_formulas,
        ocr=ocr,
        password=password,
        max_pages=max_pages,
        emit_chunks=emit_chunks,
        chunk_strategy=chunk_strategy,
        chunk_max_tokens=chunk_max_tokens,
        chunk_overlap_tokens=chunk_overlap_tokens,
        emit_html=emit_html,
        emit_text=emit_text,
    )

    manager = JobManager.get_instance()
    job = manager.submit_job(
        file_path=saved_path,
        original_filename=file.filename,
        options=options,
    )

    return AsyncParseResponse(
        job_id=job.job_id,
        status=job.status,
        source_file=file.filename or "upload.pdf",
        status_url=f"/v1/documents/jobs/{job.job_id}",
        message="Document parsing enqueued successfully",
    )


def _to_job_response(job) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.job_id,
        batch_id=job.batch_id,
        source_file=job.source_file,
        status=job.status,
        progress=JobProgressInfo(
            current_page=job.current_page,
            total_pages=job.total_pages,
            percent=job.percent,
            stage=job.stage,
        ),
        result=job.result,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@app.get("/v1/documents/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    """Retrieves live execution status, progress, and results for a parsing job."""
    job = JobManager.get_instance().get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    return _to_job_response(job)


@app.post(
    "/v1/batches/parse-async",
    response_model=BatchJobResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def parse_batch_async(
    files: list[UploadFile] = File(..., description="One or more PDF files to parse as a batch"),
    profile: ParsingProfile = Form(default=ParsingProfile.BALANCED, description="Parsing profile"),
    extract_images: bool = Form(default=True, description="Extract and save embedded raster images"),
    extract_tables: bool = Form(default=True, description="Detect and extract tabular structures"),
    extract_charts: bool = Form(default=True, description="Detect and isolate vector drawings/charts"),
    extract_formulas: bool = Form(default=True, description="Detect formulas/equations"),
    ocr: OCRMode = Form(default=OCRMode.AUTO, description="OCR mode: auto, always, never"),
    password: str | None = Form(default=None, description="Password applied to every PDF"),
    debug: bool = Form(default=False, description="Enable debug page rendering"),
    max_pages: int | None = Form(default=None, description="Page limit per document"),
    emit_chunks: bool = Form(default=False, description="Also write chunks.jsonl per document"),
    chunk_strategy: ChunkStrategy = Form(default=ChunkStrategy.HYBRID, description="Chunking strategy"),
    chunk_max_tokens: int = Form(default=512, description="Approximate token ceiling per chunk"),
    chunk_overlap_tokens: int = Form(default=64, description="Approximate overlap tokens per chunk"),
    emit_html: bool = Form(default=False, description="Also write document.html per document"),
    emit_text: bool = Form(default=False, description="Also write document.txt per document"),
):
    """Submits a multi-file batch for background parallel processing."""
    if len(files) > config.max_batch_size:
        raise HTTPException(
            status_code=400,
            detail=f"Batch size exceeds maximum of {config.max_batch_size} files per request.",
        )

    intake = _intake_dir()
    file_items: list[tuple[Path, str]] = []

    for upload in files:
        if not upload.filename or not upload.filename.lower().endswith(".pdf"):
            continue
        temp_id = uuid.uuid4().hex[:10]
        saved_path = intake / f"{temp_id}_{Path(upload.filename).name}"
        await _save_upload(upload, saved_path)
        file_items.append((saved_path, upload.filename))

    if not file_items:
        raise HTTPException(
            status_code=400,
            detail="No valid PDF files provided. Please upload at least one .pdf file.",
        )

    options = _build_options(
        profile=profile,
        debug=debug,
        extract_images=extract_images,
        extract_tables=extract_tables,
        extract_charts=extract_charts,
        extract_formulas=extract_formulas,
        ocr=ocr,
        password=password,
        max_pages=max_pages,
        emit_chunks=emit_chunks,
        chunk_strategy=chunk_strategy,
        chunk_max_tokens=chunk_max_tokens,
        chunk_overlap_tokens=chunk_overlap_tokens,
        emit_html=emit_html,
        emit_text=emit_text,
    )

    batch_id, jobs = JobManager.get_instance().submit_batch(
        file_items=file_items, options=options
    )

    return BatchJobResponse(
        batch_id=batch_id,
        total_documents=len(jobs),
        completed_count=0,
        failed_count=0,
        processing_count=0,
        queued_count=len(jobs),
        status="queued",
        jobs=[_to_job_response(j) for j in jobs],
    )


@app.get("/v1/batches/{batch_id}", response_model=BatchJobResponse)
def get_batch_status(batch_id: str):
    """Retrieves aggregated status and progress across all documents in a batch."""
    jobs = JobManager.get_instance().get_batch_status(batch_id)
    if not jobs:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found.")

    completed = sum(1 for j in jobs if j.status == "completed")
    failed = sum(1 for j in jobs if j.status == "failed")
    processing = sum(1 for j in jobs if j.status == "processing")
    queued = sum(1 for j in jobs if j.status == "queued")

    if completed == len(jobs):
        batch_status = "completed"
    elif failed == len(jobs):
        batch_status = "failed"
    elif completed + failed == len(jobs):
        batch_status = "partial_failure"
    elif processing > 0:
        batch_status = "processing"
    else:
        batch_status = "queued"

    return BatchJobResponse(
        batch_id=batch_id,
        total_documents=len(jobs),
        completed_count=completed,
        failed_count=failed,
        processing_count=processing,
        queued_count=queued,
        status=batch_status,
        jobs=[_to_job_response(j) for j in jobs],
    )
