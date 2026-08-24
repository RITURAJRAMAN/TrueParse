from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field

from trueparse.core.config import ParseOptions
from trueparse.pdf.inspector import DocumentInspection


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


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


class InspectDocumentRequest(BaseModel):
    file_path: str


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
    batch_id: Optional[str] = None
    source_file: str
    status: str  # "queued", "processing", "completed", "failed"
    progress: JobProgressInfo
    result: Optional[dict] = None
    error: Optional[str] = None
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
