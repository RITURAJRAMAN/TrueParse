from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from trueparse.api.jobs import submission_schema, submit
from trueparse.api.lifecycle import get_store
from trueparse.api.routes import require_api_key
from trueparse.contracts.jobs import BatchStatus
from trueparse.jobs.store import JobStore

router = APIRouter(prefix="/v1/batches", dependencies=[Depends(require_api_key)])


@router.post("", status_code=202, response_model=BatchStatus, openapi_extra=submission_schema(True))
async def submit_batch(request: Request, store: JobStore = Depends(get_store)):
    return await submit(request, store, True)


@router.get("/{batch_id}", response_model=BatchStatus)
def get_batch(
    batch_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    store: JobStore = Depends(get_store),
):
    store.maintain()
    return store.batch(batch_id, cursor, limit)


@router.post("/{batch_id}/cancel", response_model=BatchStatus)
def cancel_batch(batch_id: str, response: Response, store: JobStore = Depends(get_store)):
    batch = store.cancel_batch(batch_id)
    response.status_code = 202 if batch.counts.get("cancelling") else 200
    return batch
