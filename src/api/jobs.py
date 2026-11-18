from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from trueparse.api.job_uploads import receive_job_upload
from trueparse.api.lifecycle import get_store
from trueparse.api.policy import apply_limits, parse_options
from trueparse.api.routes import require_api_key
from trueparse.config.options import ResourceLimits
from trueparse.contracts.jobs import JobStatus
from trueparse.jobs.store import JobStore
from trueparse.sdk.result import ParseResult

router = APIRouter(prefix="/v1/jobs", dependencies=[Depends(require_api_key)])
_upload_slots = asyncio.Semaphore(2)


def submission_schema(batch=False):
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["files" if batch else "file"],
                        "properties": {
                            "files" if batch else "file": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                            }
                            if batch
                            else {"type": "string", "format": "binary"},
                            "options": {
                                "type": "string",
                                "default": "{}",
                                "description": "JSON ParseRequest options",
                            },
                        },
                    }
                }
            },
        }
    }


async def submit(request: Request, store: JobStore, batch=False):
    async with _upload_slots:
        stage = await run_in_threadpool(store.begin_stage)
        try:
            files, encoded = await receive_job_upload(request, store, stage, batch)
            options = apply_limits(
                parse_options(encoded),
                ResourceLimits.model_validate_json(os.environ.get("TRUEPARSE_RESOURCE_LIMITS", "{}")),
            )
            identifier = await run_in_threadpool(
                store.admit, stage, files, options, batch=batch, key=request.headers.get("Idempotency-Key")
            )
            return await run_in_threadpool(store.batch if batch else store.get, identifier)
        finally:
            await run_in_threadpool(store.discard_stage, stage)


@router.post("", status_code=202, response_model=JobStatus, openapi_extra=submission_schema())
async def submit_job(request: Request, store: JobStore = Depends(get_store)):
    return await submit(request, store)


@router.get("/{job_id}", response_model=JobStatus)
def get_job(job_id: str, store: JobStore = Depends(get_store)):
    store.maintain()
    return store.get(job_id)


@router.get("/{job_id}/result", response_model=ParseResult)
def get_job_result(job_id: str, store: JobStore = Depends(get_store)):
    store.maintain()
    return Response(store.result(job_id), media_type="application/json")


@router.post("/{job_id}/cancel", response_model=JobStatus)
def cancel_job(job_id: str, response: Response, store: JobStore = Depends(get_store)):
    job = store.cancel(job_id)
    response.status_code = 202 if job.state == "cancelling" else 200
    return job
