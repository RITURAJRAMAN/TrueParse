from __future__ import annotations
import sqlite3
import json
import time
import os
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, asdict


@dataclass
class JobRecord:
    job_id: str
    batch_id: Optional[str]
    source_file: str
    file_path: str
    status: str  # "queued", "processing", "completed", "failed"
    current_page: int
    total_pages: int
    percent: float
    stage: str
    result: Optional[dict[str, Any]]
    error: Optional[str]
    created_at: float
    updated_at: float


class SQLiteJobStore:
    """Thread-safe and process-safe SQLite persistent job repository."""

    def __init__(self, db_path: str | Path = "data/jobs.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    batch_id TEXT,
                    source_file TEXT,
                    file_path TEXT,
                    status TEXT,
                    current_page INTEGER,
                    total_pages INTEGER,
                    percent REAL,
                    stage TEXT,
                    result TEXT,
                    error TEXT,
                    created_at REAL,
                    updated_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_batch_id ON jobs(batch_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)")
            conn.commit()

    def create_job(
        self,
        job_id: str,
        source_file: str,
        file_path: str,
        batch_id: Optional[str] = None,
        total_pages: int = 0,
    ) -> JobRecord:
        now = time.time()
        job = JobRecord(
            job_id=job_id,
            batch_id=batch_id,
            source_file=source_file,
            file_path=file_path,
            status="queued",
            current_page=0,
            total_pages=total_pages,
            percent=0.0,
            stage="queued",
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, batch_id, source_file, file_path, status,
                    current_page, total_pages, percent, stage,
                    result, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.batch_id,
                    job.source_file,
                    job.file_path,
                    job.status,
                    job.current_page,
                    job.total_pages,
                    job.percent,
                    job.stage,
                    None,
                    None,
                    job.created_at,
                    job.updated_at,
                ),
            )
            conn.commit()
        return job

    def update_progress(
        self,
        job_id: str,
        current_page: int,
        total_pages: int,
        stage: str,
        status: str = "processing",
    ) -> None:
        percent = (current_page / max(1, total_pages)) * 100.0 if total_pages > 0 else 0.0
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE jobs SET
                    current_page = ?,
                    total_pages = ?,
                    percent = ?,
                    stage = ?,
                    status = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (current_page, total_pages, round(percent, 1), stage, status, now, job_id),
            )
            conn.commit()

    def complete_job(self, job_id: str, result: dict[str, Any]) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE jobs SET
                    status = 'completed',
                    percent = 100.0,
                    stage = 'completed',
                    result = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (json.dumps(result), now, job_id),
            )
            conn.commit()

    def fail_job(self, job_id: str, error: str) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE jobs SET
                    status = 'failed',
                    stage = 'failed',
                    error = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (error, now, job_id),
            )
            conn.commit()

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return JobRecord(
                job_id=row["job_id"],
                batch_id=row["batch_id"],
                source_file=row["source_file"],
                file_path=row["file_path"],
                status=row["status"],
                current_page=row["current_page"],
                total_pages=row["total_pages"],
                percent=row["percent"],
                stage=row["stage"],
                result=json.loads(row["result"]) if row["result"] else None,
                error=row["error"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def get_batch_jobs(self, batch_id: str) -> list[JobRecord]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE batch_id = ? ORDER BY created_at ASC",
                (batch_id,),
            ).fetchall()
            jobs = []
            for row in rows:
                jobs.append(
                    JobRecord(
                        job_id=row["job_id"],
                        batch_id=row["batch_id"],
                        source_file=row["source_file"],
                        file_path=row["file_path"],
                        status=row["status"],
                        current_page=row["current_page"],
                        total_pages=row["total_pages"],
                        percent=row["percent"],
                        stage=row["stage"],
                        result=json.loads(row["result"]) if row["result"] else None,
                        error=row["error"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
            return jobs
