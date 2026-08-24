from __future__ import annotations
import os
import uuid
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, Future
from pathlib import Path
from typing import Optional, Any

from trueparse.core.config import ParseOptions, EngineConfig
from trueparse.pipeline.runner import PDFParser
from trueparse.workers.job_store import SQLiteJobStore, JobRecord

logger = logging.getLogger("ParsingEngine")


def _execute_parse_worker(
    job_id: str,
    file_path: str,
    original_filename: str,
    options_dict: dict[str, Any],
    db_path: str,
) -> dict[str, Any]:
    """Worker function that runs in a background thread/process."""
    store = SQLiteJobStore(db_path=db_path)
    try:
        def on_progress(current_page: int, total_pages: int, stage: str):
            store.update_progress(
                job_id=job_id,
                current_page=current_page,
                total_pages=total_pages,
                stage=stage,
                status="processing",
            )

        store.update_progress(job_id=job_id, current_page=0, total_pages=1, stage="initializing", status="processing")
        options = ParseOptions(**options_dict)
        parser = PDFParser(options=options)
        
        doc = parser.parse(
            file_path=file_path,
            original_filename=original_filename,
            progress_callback=on_progress,
        )
        out_dir = parser.storage.get_document_dir(doc.id)

        result_payload = {
            "document_id": doc.id,
            "source_file": original_filename,
            "page_count": len(doc.pages),
            "assets_count": len(doc.assets),
            "sections_count": len(doc.sections),
            "quality_score": doc.quality.overall_score,
            "document_path": str(out_dir / "output" / "document.json"),
            "markdown_path": str(out_dir / "output" / "document.md"),
            "asset_root": str(out_dir / "assets"),
            "warnings": doc.warnings,
        }
        store.complete_job(job_id=job_id, result=result_payload)
        logger.info(f"Background Job {job_id} completed successfully for {original_filename}")
        return result_payload

    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        logger.error(f"Background Job {job_id} failed: {err_msg}")
        store.fail_job(job_id=job_id, error=str(e))
        raise


class JobManager:
    """Singleton Background Queue & Task Manager backed by SQLite."""

    _instance: Optional[JobManager] = None

    def __init__(self, max_workers: Optional[int] = None, db_path: str = "data/jobs.db"):
        self.db_path = db_path
        self.store = SQLiteJobStore(db_path=db_path)
        cores = os.cpu_count() or 4
        workers = max_workers or min(32, max(2, cores))
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="PDFWorker")
        self._futures: dict[str, Future] = {}
        logger.info(f"Initialized JobManager with {workers} parallel worker threads")

    @classmethod
    def get_instance(cls, max_workers: Optional[int] = None, db_path: str = "data/jobs.db") -> JobManager:
        if cls._instance is None:
            cls._instance = cls(max_workers=max_workers, db_path=db_path)
        return cls._instance

    def submit_job(
        self,
        file_path: str | Path,
        original_filename: Optional[str] = None,
        options: Optional[ParseOptions] = None,
        batch_id: Optional[str] = None,
    ) -> JobRecord:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        path_str = str(Path(file_path).resolve())
        src_name = original_filename or Path(file_path).name
        opts = options or ParseOptions()
        opts_dict = opts.model_dump()

        job = self.store.create_job(
            job_id=job_id,
            source_file=src_name,
            file_path=path_str,
            batch_id=batch_id,
        )

        future = self.executor.submit(
            _execute_parse_worker,
            job_id=job_id,
            file_path=path_str,
            original_filename=src_name,
            options_dict=opts_dict,
            db_path=self.db_path,
        )
        self._futures[job_id] = future
        logger.info(f"Enqueued background parsing job: {job_id} for file {src_name}")
        return job

    def submit_batch(
        self,
        file_items: list[tuple[str | Path, Optional[str]]],
        options: Optional[ParseOptions] = None,
    ) -> tuple[str, list[JobRecord]]:
        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        jobs: list[JobRecord] = []
        for file_path, orig_name in file_items:
            job = self.submit_job(
                file_path=file_path,
                original_filename=orig_name,
                options=options,
                batch_id=batch_id,
            )
            jobs.append(job)
        logger.info(f"Enqueued batch {batch_id} with {len(jobs)} document parsing tasks")
        return batch_id, jobs

    def get_job_status(self, job_id: str) -> Optional[JobRecord]:
        return self.store.get_job(job_id)

    def get_batch_status(self, batch_id: str) -> list[JobRecord]:
        return self.store.get_batch_jobs(batch_id)
