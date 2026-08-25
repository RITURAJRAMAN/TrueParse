from __future__ import annotations

from pydantic import BaseModel, Field

from trueparse.core.version import get_version
from trueparse.pdf.inspector import DocumentInspection


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = Field(default_factory=get_version)
    ocr_available: bool = Field(
        default=False,
        description="True when an OCR backend is installed and loadable",
    )
    auth_required: bool = Field(
        default=False,
        description="True when TRUEPARSE_API_KEY is set and X-API-Key is enforced",
    )
    output_root: str = Field(
        default="",
        description="Server-controlled directory all parsing output is written to",
    )


class ParseDocumentResponse(BaseModel):
    request_id: str
    document_id: str
    status: str = "completed"
    schema_version: str = "1.0"
    document_path: str
    asset_root: str
    page_count: int
    assets_count: int
    quality_score: float
    warnings: list[str] = Field(default_factory=list)


class InspectDocumentResponse(BaseModel):
    inspection: DocumentInspection


class AsyncParseResponse(BaseModel):
    job_id: str
    status: str = "queued"
    source_file: str
    status_url: str
    message: str = "Document parsing enqueued successfully"


class JobProgressInfo(BaseModel):
    current_page: int = 0
    total_pages: int = 0
    percent: float = 0.0
    stage: str = "queued"


class JobStatusResponse(BaseModel):
    job_id: str
    batch_id: str | None = None
    source_file: str
    status: str  # "queued", "processing", "completed", "failed"
    progress: JobProgressInfo
    result: dict | None = None
    error: str | None = None
    created_at: float
    updated_at: float


class BatchJobResponse(BaseModel):
    batch_id: str
    total_documents: int
    completed_count: int
    failed_count: int
    processing_count: int
    queued_count: int
    status: str  # "queued", "processing", "completed", "partial_failure", "failed"
    jobs: list[JobStatusResponse]
