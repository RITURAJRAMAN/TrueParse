from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JobSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    root: Path = Path("data/jobs")
    workers: int = Field(default=2, ge=1, le=64)
    queue_capacity: int = Field(default=100, ge=1)
    max_batch_files: int = Field(default=20, ge=1, le=1000)
    max_upload_bytes: int = Field(default=200 * 1024 * 1024, ge=1)
    disk_quota_bytes: int = Field(default=2 * 1024**3, ge=1)
    retention_seconds: float = Field(default=86400, gt=0)
    tombstone_seconds: float = Field(default=86400, gt=0)
    heartbeat_seconds: float = Field(default=5, gt=0)
    lease_seconds: float = Field(default=30, gt=0)
    staging_seconds: float = Field(default=300, gt=0)
    max_attempts: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def timing(self):
        if self.lease_seconds <= 2 * self.heartbeat_seconds:
            raise ValueError("lease_seconds must exceed twice heartbeat_seconds")
        return self

    @classmethod
    def from_env(cls) -> JobSettings:
        return cls.model_validate(
            {
                name: os.environ[f"TRUEPARSE_JOBS_{name.upper()}"]
                for name in cls.model_fields
                if f"TRUEPARSE_JOBS_{name.upper()}" in os.environ
            }
        )


def jobs_enabled() -> bool:
    return os.environ.get("TRUEPARSE_JOBS_ENABLED", "true").lower() in ("1", "true", "yes")
