from __future__ import annotations

import logging
import os
import traceback
import uuid
from collections.abc import Sequence
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from trueparse.core.config import ParseOptions
from trueparse.pipeline.runner import PDFParser
from trueparse.workers.job_store import JobRecord, SQLiteJobStore

logger = logging.getLogger("trueparse")

#: Set to "thread" to force the thread pool instead of subprocesses.
ENV_WORKER_MODE = "TRUEPARSE_WORKER_MODE"

#: Overrides the automatically chosen worker count.
ENV_MAX_WORKERS = "TRUEPARSE_MAX_WORKERS"


def _execute_parse_worker(
    job_id: str,
    file_path: str,
    original_filename: str,
    options_dict: dict[str, Any],
    db_path: str,
) -> dict[str, Any]:
    """Worker entry point. Runs in a background process (or thread)."""
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

        store.update_progress(
            job_id=job_id, current_page=0, total_pages=1,
            stage="initializing", status="processing",
        )
        options = ParseOptions(**options_dict)
        parser = PDFParser(options=options)

        doc = parser.parse(
            file_path=file_path,
            original_filename=original_filename,
            progress_callback=on_progress,
        )
        out_dir = parser.storage.get_document_dir(doc.id)
        output_dir = out_dir / "output"

        result_payload: dict[str, Any] = {
            "document_id": doc.id,
            "source_file": original_filename,
            "page_count": len(doc.pages),
            "assets_count": len(doc.assets),
            "sections_count": len(doc.sections),
            "quality_score": doc.quality.overall_score,
            "document_path": str(output_dir / "document.json"),
            "markdown_path": str(output_dir / "document.md"),
            "asset_root": str(out_dir / "assets"),
            "warnings": doc.warnings,
        }
        if options.emit_chunks:
            result_payload["chunks_path"] = str(output_dir / "chunks.jsonl")
        if options.emit_html:
            result_payload["html_path"] = str(output_dir / "document.html")
        if options.emit_text:
            result_payload["text_path"] = str(output_dir / "document.txt")

        store.complete_job(job_id=job_id, result=result_payload)
        logger.info(f"Background Job {job_id} completed successfully for {original_filename}")
        return result_payload

    except Exception as e:
        logger.error(
            f"Background Job {job_id} failed: {type(e).__name__}: {e}\n{traceback.format_exc()}"
        )
        store.fail_job(job_id=job_id, error=f"{type(e).__name__}: {e}")
        raise


class JobManager:
    """Singleton background queue & task manager backed by SQLite.

    PDF parsing is CPU-bound, so work runs in a **process** pool by default;
    the previous thread pool serialised on the GIL and gained little from its
    32 workers. Set ``TRUEPARSE_WORKER_MODE=thread`` to opt back into threads.
    """

    _instance: JobManager | None = None

    def __init__(
        self,
        max_workers: int | None = None,
        db_path: str = "data/jobs.db",
        worker_mode: str | None = None,
    ):
        self.db_path = db_path
        self.store = SQLiteJobStore(db_path=db_path)

        cores = os.cpu_count() or 4
        env_workers = os.environ.get(ENV_MAX_WORKERS, "").strip()
        if max_workers is None and env_workers:
            try:
                max_workers = int(env_workers)
            except ValueError:
                pass
        # One worker per core, capped: each holds a whole PDF in memory.
        workers = max_workers or max(2, min(8, cores))

        mode = (worker_mode or os.environ.get(ENV_WORKER_MODE, "process")).lower()
        self.worker_mode = "thread" if mode == "thread" else "process"
        self.executor: Executor = self._create_executor(workers)
        self._futures: dict[str, Future] = {}

        recovered = self.store.fail_stale_jobs(
            "Worker process restarted before this job finished."
        )
        if recovered:
            logger.warning(
                f"Marked {recovered} job(s) left mid-flight by a previous run as failed"
            )

        logger.info(
            f"Initialized JobManager with {workers} parallel {self.worker_mode} workers"
        )

    def _create_executor(self, workers: int) -> Executor:
        if self.worker_mode == "thread":
            return ThreadPoolExecutor(max_workers=workers, thread_name_prefix="PDFWorker")
        try:
            return ProcessPoolExecutor(max_workers=workers)
        except (OSError, ValueError) as exc:
            # Some sandboxes forbid subprocesses; degrade rather than fail.
            logger.warning(
                f"Process pool unavailable ({exc}); falling back to thread workers."
            )
            self.worker_mode = "thread"
            return ThreadPoolExecutor(max_workers=workers, thread_name_prefix="PDFWorker")

    @classmethod
    def get_instance(
        cls,
        max_workers: int | None = None,
        db_path: str = "data/jobs.db",
    ) -> JobManager:
        if cls._instance is None:
            cls._instance = cls(max_workers=max_workers, db_path=db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Drops the singleton and shuts its pool down. Intended for tests."""
        if cls._instance is not None:
            cls._instance.executor.shutdown(wait=False, cancel_futures=True)
            cls._instance = None

    def submit_job(
        self,
        file_path: str | Path,
        original_filename: str | None = None,
        options: ParseOptions | None = None,
        batch_id: str | None = None,
    ) -> JobRecord:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        path_str = str(Path(file_path).resolve())
        src_name = original_filename or Path(file_path).name
        opts = options or ParseOptions()

        job = self.store.create_job(
            job_id=job_id,
            source_file=src_name,
            file_path=path_str,
            batch_id=batch_id,
        )

        future = self.executor.submit(
            _execute_parse_worker,
            job_id,
            path_str,
            src_name,
            opts.model_dump(mode="json"),
            self.db_path,
        )
        self._futures[job_id] = future
        logger.info(f"Enqueued background parsing job: {job_id} for file {src_name}")
        return job

    def submit_batch(
        self,
        file_items: Sequence[tuple[str | Path, str | None]],
        options: ParseOptions | None = None,
    ) -> tuple[str, list[JobRecord]]:
        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        jobs = [
            self.submit_job(
                file_path=file_path,
                original_filename=orig_name,
                options=options,
                batch_id=batch_id,
            )
            for file_path, orig_name in file_items
        ]
        logger.info(f"Enqueued batch {batch_id} with {len(jobs)} document parsing tasks")
        return batch_id, jobs

    def get_job_status(self, job_id: str) -> JobRecord | None:
        return self.store.get_job(job_id)

    def get_batch_status(self, batch_id: str) -> list[JobRecord]:
        return self.store.get_batch_jobs(batch_id)
