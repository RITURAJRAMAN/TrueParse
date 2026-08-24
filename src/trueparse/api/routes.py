from __future__ import annotations
import uuid
import tempfile
import os
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from trueparse.core.logging import setup_logging
from trueparse.core.config import ParseOptions, EngineConfig
from trueparse.core.enums import ParsingProfile, OCRMode
from trueparse.pipeline.runner import PDFParser
from trueparse.pdf.inspector import PDFInspector
from trueparse.serializer.json import JSONSerializer
from trueparse.workers.manager import JobManager
from trueparse.api.schemas import (
    HealthResponse,
    ParseDocumentResponse,
    InspectDocumentRequest,
    InspectDocumentResponse,
    AsyncParseResponse,
    JobStatusResponse,
    BatchJobResponse,
    JobProgressInfo,
)

setup_logging()

app = FastAPI(
    title="TrueParse API",
    version="0.1.1",
    description="Local-first canonical PDF parsing and document understanding REST service.",
)

config = EngineConfig()


@app.get("/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="ok", version=config.engine_version)


@app.post("/v1/documents/parse", response_model=ParseDocumentResponse)
async def parse_document(
    file: UploadFile = File(..., description="PDF file to parse"),
    output_path: str = Form(
        default="data/output",
        description="Root output directory where results and assets are saved",
    ),
    profile: ParsingProfile = Form(
        default=ParsingProfile.BALANCED,
        description="Parsing profile: fast, balanced, accurate, maximum_accuracy",
    ),
    debug: bool = Form(
        default=False,
        description="Enable debug page rendering",
    ),
    extract_images: bool = Form(
        default=True,
        description="Extract embedded visual raster images",
    ),
    extract_tables: bool = Form(
        default=True,
        description="Detect and structurally reconstruct tables",
    ),
    extract_charts: bool = Form(
        default=True,
        description="Detect and extract charts/diagrams",
    ),
    extract_formulas: bool = Form(
        default=True,
        description="Detect and extract mathematical equations/formulas",
    ),
    ocr: OCRMode = Form(
        default=OCRMode.AUTO,
        description="OCR mode: auto, always, never",
    ),
    max_pages: Optional[int] = Form(
        default=None,
        description="Limit maximum number of pages to process. Leave empty, null, or 0 to process ALL pages.",
    ),
):
    req_id = f"req_{uuid.uuid4().hex[:8]}"

    # Save uploaded file to temp file
    suffix = Path(file.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        options = ParseOptions(
            profile=profile,
            debug=debug,
            extract_images=extract_images,
            extract_tables=extract_tables,
            extract_charts=extract_charts,
            extract_formulas=extract_formulas,
            ocr=ocr,
            output_path=output_path,
            max_pages=max_pages if max_pages and max_pages > 0 else None,
        )
        parser = PDFParser(options=options, config=config)
        doc = parser.parse(tmp_path, original_filename=file.filename)

        doc_dir = parser.storage.get_document_dir(doc.id)
        doc_json_path = doc_dir / "output" / "document.json"
        asset_root_path = doc_dir / "assets"

        return ParseDocumentResponse(
            request_id=req_id,
            document_id=doc.id,
            status="completed",
            schema_version=doc.schema_version,
            document_path=str(doc_json_path.resolve()),
            asset_root=str(asset_root_path.resolve()),
            page_count=len(doc.pages),
            assets_count=len(doc.assets),
            quality_score=doc.quality.overall_score,
            warnings=doc.warnings,
        )
    finally:
        if tmp_path.exists():
            try:
                os.remove(tmp_path)
            except Exception:
                pass


@app.post("/v1/documents/inspect", response_model=InspectDocumentResponse)
def inspect_document(req: InspectDocumentRequest):
    p = Path(req.file_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")
    inspection = PDFInspector.inspect(p)
    return InspectDocumentResponse(inspection=inspection)


@app.get("/v1/documents/{document_id}/json")
def get_document_json(document_id: str, output_root: Optional[str] = Query(None)):
    root = output_root or config.default_output_root
    json_path = Path(root) / document_id / "output" / "document.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found.")
    return FileResponse(json_path, media_type="application/json")


@app.get("/v1/documents/{document_id}/assets/{asset_id}")
def get_document_asset(document_id: str, asset_id: str, output_root: Optional[str] = Query(None)):
    root = output_root or config.default_output_root
    assets_dir = Path(root) / document_id / "assets"
    # Find file matching asset_id in images, figures, charts, diagrams
    for sub in ["images", "figures", "charts", "diagrams"]:
        target_sub = assets_dir / sub
        if target_sub.exists():
            for f in target_sub.iterdir():
                if f.stem == asset_id:
                    return FileResponse(f)
    raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found for document {document_id}")


# ==========================================
# Asynchronous Job & Batch Processing Endpoints
# ==========================================

@app.post("/v1/documents/parse-async", response_model=AsyncParseResponse, status_code=202)
async def parse_document_async(
    file: UploadFile = File(..., description="PDF file to parse asynchronously"),
    output_path: str = Form(
        default="data/output",
        description="Root output directory where results and assets are saved",
    ),
    profile: ParsingProfile = Form(
        default=ParsingProfile.BALANCED,
        description="Parsing profile: fast, balanced, accurate, maximum_accuracy",
    ),
    extract_images: bool = Form(default=True, description="Extract and save embedded raster images"),
    extract_tables: bool = Form(default=True, description="Detect and extract tabular structures"),
    extract_charts: bool = Form(default=True, description="Detect and isolate vector visual drawings/charts"),
    extract_formulas: bool = Form(default=True, description="Detect formulas/equations"),
    ocr: OCRMode = Form(default=OCRMode.AUTO, description="OCR mode: auto, always, never"),
    debug: bool = Form(
        default=False,
        description="Enable debug page rendering (saves full-page PNGs to debug/pages/)",
    ),
    max_pages: Optional[int] = Form(
        default=None,
        description="Limit maximum number of pages to process. Leave empty, null, or 0 to process ALL pages.",
    ),
):
    """Submits a PDF for non-blocking asynchronous processing and immediately returns a Job ID."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # Save intake copy
    intake_dir = Path("data/intake")
    intake_dir.mkdir(parents=True, exist_ok=True)
    temp_id = uuid.uuid4().hex[:10]
    saved_path = intake_dir / f"{temp_id}_{file.filename}"

    with open(saved_path, "wb") as f:
        content = await file.read()
        f.write(content)

    options = ParseOptions(
        profile=profile,
        extract_images=extract_images,
        extract_tables=extract_tables,
        extract_charts=extract_charts,
        extract_formulas=extract_formulas,
        ocr=ocr,
        output_path=output_path,
        debug=debug,
        max_pages=max_pages if max_pages and max_pages > 0 else None,
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
        source_file=file.filename,
        status_url=f"/v1/documents/jobs/{job.job_id}",
        message="Document parsing enqueued successfully",
    )


@app.get("/v1/documents/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    """Retrieves live execution status, progress, and results for an asynchronous parsing job."""
    manager = JobManager.get_instance()
    job = manager.get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")

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


@app.post(
    "/v1/batches/parse-async",
    response_model=BatchJobResponse,
    status_code=202,
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "format": "binary",
                                },
                                "description": "Select one or more PDF files to upload",
                            },
                            "output_path": {
                                "type": "string",
                                "default": "data/output",
                                "description": "Root output directory where results and assets are saved",
                            },
                            "profile": {
                                "type": "string",
                                "enum": ["fast", "balanced", "accurate", "maximum_accuracy"],
                                "default": "balanced",
                                "description": "Parsing profile",
                            },
                            "extract_images": {
                                "type": "boolean",
                                "default": True,
                                "description": "Extract and save embedded raster images",
                            },
                            "extract_tables": {
                                "type": "boolean",
                                "default": True,
                                "description": "Detect and extract tabular structures",
                            },
                            "extract_charts": {
                                "type": "boolean",
                                "default": True,
                                "description": "Detect and isolate vector visual drawings/charts",
                            },
                            "extract_formulas": {
                                "type": "boolean",
                                "default": True,
                                "description": "Detect formulas/equations",
                            },
                            "ocr": {
                                "type": "string",
                                "enum": ["auto", "always", "never"],
                                "default": "auto",
                                "description": "OCR mode: auto, always, never",
                            },
                            "debug": {
                                "type": "boolean",
                                "default": False,
                                "description": "Enable debug page rendering (saves full-page PNGs to debug/pages/)",
                            },
                            "max_pages": {
                                "type": "integer",
                                "nullable": True,
                                "description": "Limit maximum number of pages per document (leave empty or 0 for all pages)",
                            },
                        },
                        "required": ["files"],
                    }
                }
            }
        }
    },
)
async def parse_batch_async(
    files: list[UploadFile] = File(
        ...,
        description="Select one or more PDF files to parse concurrently as a batch",
    ),
    output_path: str = Form(
        default="data/output",
        description="Root output directory where results and assets are saved",
    ),
    profile: ParsingProfile = Form(
        default=ParsingProfile.BALANCED,
        description="Parsing profile: fast, balanced, accurate, maximum_accuracy",
    ),
    extract_images: bool = Form(default=True, description="Extract and save embedded raster images"),
    extract_tables: bool = Form(default=True, description="Detect and extract tabular structures"),
    extract_charts: bool = Form(default=True, description="Detect and isolate vector visual drawings/charts"),
    extract_formulas: bool = Form(default=True, description="Detect formulas/equations"),
    ocr: OCRMode = Form(default=OCRMode.AUTO, description="OCR mode: auto, always, never"),
    debug: bool = Form(
        default=False,
        description="Enable debug page rendering (saves full-page PNGs to debug/pages/)",
    ),
    max_pages: Optional[int] = Form(
        default=None,
        description="Limit maximum number of pages per document. Leave empty, null, or 0 to process ALL pages.",
    ),
):
    """Submits a multi-file batch for background parallel processing across worker threads."""
    if len(files) > config.max_batch_size:
        raise HTTPException(
            status_code=400,
            detail=f"Batch size exceeds maximum allowed limit of {config.max_batch_size} files per request.",
        )

    intake_dir = Path("data/intake")
    intake_dir.mkdir(parents=True, exist_ok=True)
    file_items = []

    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            continue
        temp_id = uuid.uuid4().hex[:10]
        saved_path = intake_dir / f"{temp_id}_{file.filename}"
        with open(saved_path, "wb") as f:
            content = await file.read()
            f.write(content)
        file_items.append((saved_path, file.filename))

    if not file_items:
        raise HTTPException(
            status_code=400,
            detail="No valid PDF files provided. Please upload at least one .pdf file.",
        )

    options = ParseOptions(
        profile=profile,
        extract_images=extract_images,
        extract_tables=extract_tables,
        extract_charts=extract_charts,
        extract_formulas=extract_formulas,
        ocr=ocr,
        output_path=output_path,
        debug=debug,
        max_pages=max_pages if max_pages and max_pages > 0 else None,
    )

    manager = JobManager.get_instance()
    batch_id, jobs = manager.submit_batch(file_items=file_items, options=options)

    job_responses = [
        JobStatusResponse(
            job_id=j.job_id,
            batch_id=j.batch_id,
            source_file=j.source_file,
            status=j.status,
            progress=JobProgressInfo(
                current_page=j.current_page,
                total_pages=j.total_pages,
                percent=j.percent,
                stage=j.stage,
            ),
            result=j.result,
            error=j.error,
            created_at=j.created_at,
            updated_at=j.updated_at,
        )
        for j in jobs
    ]

    return BatchJobResponse(
        batch_id=batch_id,
        total_documents=len(jobs),
        completed_count=0,
        failed_count=0,
        processing_count=0,
        queued_count=len(jobs),
        status="queued",
        jobs=job_responses,
    )


@app.get("/v1/batches/{batch_id}", response_model=BatchJobResponse)
def get_batch_status(batch_id: str):
    """Retrieves aggregated status and progress across all documents in a batch."""
    manager = JobManager.get_instance()
    jobs = manager.get_batch_status(batch_id)
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

    job_responses = [
        JobStatusResponse(
            job_id=j.job_id,
            batch_id=j.batch_id,
            source_file=j.source_file,
            status=j.status,
            progress=JobProgressInfo(
                current_page=j.current_page,
                total_pages=j.total_pages,
                percent=j.percent,
                stage=j.stage,
            ),
            result=j.result,
            error=j.error,
            created_at=j.created_at,
            updated_at=j.updated_at,
        )
        for j in jobs
    ]

    return BatchJobResponse(
        batch_id=batch_id,
        total_documents=len(jobs),
        completed_count=completed,
        failed_count=failed,
        processing_count=processing,
        queued_count=queued,
        status=batch_status,
        jobs=job_responses,
    )

