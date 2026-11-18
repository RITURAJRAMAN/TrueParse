from __future__ import annotations

import asyncio
import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from starlette.concurrency import run_in_threadpool

from trueparse._version import get_version
from trueparse.api.lifecycle import get_store
from trueparse.api.policy import apply_limits
from trueparse.api.settings import api_key
from trueparse.api.uploads import _upload
from trueparse.config.options import ResourceLimits
from trueparse.extraction.ocr.engine import ocr_available
from trueparse.runtime.execution import _parse_request
from trueparse.sdk.result import ParseResult

router = APIRouter()

# Bound concurrently buffered requests and isolated parsing workers.
_slots = asyncio.Semaphore(2)
_resource_ceilings = ResourceLimits.model_validate_json(os.environ.get("TRUEPARSE_RESOURCE_LIMITS", "{}"))
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(provided: str | None = Security(_api_key_header)) -> None:
    expected = api_key()
    if expected and not hmac.compare_digest((provided or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")


@router.get("/health")
def health(request: Request):
    if getattr(request.app.state, "job_worker", None) is not None:
        get_store(request)
    return {
        "status": "ok",
        "version": get_version(),
        "ocr_available": ocr_available(),
        "auth_required": api_key() is not None,
    }


@router.post(
    "/v1/parse",
    response_model=ParseResult,
    dependencies=[Depends(require_api_key)],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "options": {
                                "type": "string",
                                "default": "{}",
                                "description": 'JSON object using ParseRequest. Example: {"formats":["markdown","json"],"images":"base64"}',
                            },
                        },
                    }
                },
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
            },
        },
    },
)
async def parse_document(request: Request):
    async with _slots:
        content, filename, options = await _upload(request)
        options = apply_limits(options, _resource_ceilings)
        return await run_in_threadpool(_parse_request, content, options, filename)
