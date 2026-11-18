from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trueparse.sdk.result import ParseResult

JobState = Literal["queued", "running", "cancelling", "succeeded", "failed", "cancelled", "expired"]
BatchState = Literal["queued", "running", "succeeded", "failed", "cancelled", "mixed", "expired"]
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    page: int | None = None
    element_id: str | None = None


class JobProgress(BaseModel):
    stage: str = "queued"
    current_page: int = Field(default=0, ge=0)
    total_pages: int = Field(default=0, ge=0)


class JobStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    batch_id: str | None = None
    input_index: int = Field(ge=0)
    filename: str
    state: JobState
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    expires_at: float | None = None
    attempt: int = 0
    max_attempts: int
    progress: JobProgress = Field(default_factory=JobProgress)
    document_id: str | None = None
    error: JobError | None = None
    status_url: str
    result_url: str


class BatchStatus(BaseModel):
    batch_id: str
    state: BatchState
    total: int
    counts: dict[str, int]
    jobs: list[JobStatus]
    next_cursor: int | None = None
    status_url: str


class ParseOutcome(BaseModel):
    input_index: int
    filename: str
    result: ParseResult | None = None
    error: JobError | None = None

    @model_validator(mode="after")
    def one_outcome(self):
        if (self.result is None) == (self.error is None):
            raise ValueError("Exactly one result or error is required")
        return self
