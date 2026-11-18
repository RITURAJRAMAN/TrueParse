from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from trueparse.api.settings import max_upload_bytes
from trueparse.jobs.settings import JobSettings, jobs_enabled
from trueparse.jobs.store import JobServiceError, JobStore
from trueparse.jobs.worker import JobWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    max_upload_bytes()
    worker = JobWorker(JobStore(JobSettings.from_env())) if jobs_enabled() else None
    thread = threading.Thread(target=worker.run, name="trueparse-jobs", daemon=True) if worker else None
    app.state.job_worker = worker
    app.state.job_thread = thread
    try:
        if thread:
            thread.start()
        yield
    finally:
        if worker and thread:
            worker.stop.set()
            if thread.ident is not None:
                await asyncio.to_thread(thread.join)
            else:
                worker.close()
        app.state.job_worker = None
        app.state.job_thread = None


def get_store(request: Request) -> JobStore:
    worker = getattr(request.app.state, "job_worker", None)
    thread = getattr(request.app.state, "job_thread", None)
    if worker is None:
        raise JobServiceError(503, "JOBS_DISABLED", "The API job service is disabled or has not started.")
    if thread is None or not thread.is_alive():
        raise JobServiceError(
            503, "WORKER_UNAVAILABLE", "The API job worker stopped. Restart the API service."
        )
    return worker.store
