from __future__ import annotations

import asyncio
import json
import math
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

from pydantic import ValidationError

from trueparse.contracts.jobs import TERMINAL, BatchStatus, JobStatus
from trueparse.contracts.request import ParseRequest
from trueparse.exceptions import PDFEngineError
from trueparse.io.sources import _source_bytes
from trueparse.models.enums import ErrorCode, ImageInclusionMode
from trueparse.sdk.result import ParseResult


def request_options(options):
    try:
        request = ParseRequest.model_validate(options)
    except ValidationError as exc:
        raise PDFEngineError(ErrorCode.INVALID_OPTIONS, "Invalid parsing options.") from exc
    if request.images == ImageInclusionMode.PATH:
        raise PDFEngineError(ErrorCode.INVALID_OPTIONS, "Remote jobs require images=none or base64.")
    return request


@contextmanager
def upload_files(sources, field, filename=None):
    total = 0
    files = []
    with ExitStack() as stack:
        for index, source in enumerate(sources):
            stream: BinaryIO | bytes
            if index >= 1000:
                raise PDFEngineError(ErrorCode.INVALID_OPTIONS, "At most 1000 files may be submitted.")
            if isinstance(source, (str, Path)):
                path = Path(source)
                stream = stack.enter_context(path.open("rb"))
                size = path.stat().st_size
                name = filename or path.name
            else:
                content, name = _source_bytes(source, filename)
                stream = content
                size = len(content)
            total += size
            if total > 200 * 1024**2:
                raise PDFEngineError(ErrorCode.PDF_RESOURCE_LIMIT, "Client upload exceeds 200 MiB.")
            files.append((field, (name, stream, "application/pdf")))
        if not files:
            raise PDFEngineError(ErrorCode.INVALID_OPTIONS, "Provide at least one PDF.")
        yield files


class JobClientMethods:
    """Durable job operations implemented through the client's bounded transport."""

    def _job_request(self, method, path, model, *, max_response_bytes=128 * 1024**2, **kwargs):
        raise NotImplementedError

    def submit_job(self, source, *, filename=None, idempotency_key=None, **options) -> JobStatus:
        request = request_options(options)
        with upload_files([source], "file", filename) as files:
            return self._job_request(
                "POST",
                "/v1/jobs",
                JobStatus,
                files=files,
                data={"options": json.dumps(request.model_dump(mode="json", exclude_unset=True))},
                headers={"Idempotency-Key": idempotency_key} if idempotency_key else {},
            )

    def get_job(self, job_id: str) -> JobStatus:
        return self._job_request("GET", self._job_path(job_id), JobStatus)

    def get_job_result(self, job_id: str, *, max_response_bytes=128 * 1024**2) -> ParseResult:
        return self._job_request(
            "GET", self._job_path(job_id) + "/result", ParseResult, max_response_bytes=max_response_bytes
        )

    def cancel_job(self, job_id: str) -> JobStatus:
        return self._job_request("POST", self._job_path(job_id) + "/cancel", JobStatus)

    def wait_for_job(self, job_id: str, *, poll_interval=1.0, timeout=None) -> JobStatus:
        self._poll_options(poll_interval, timeout)
        start = time.monotonic()
        while True:
            remaining = None if timeout is None else timeout - (time.monotonic() - start)
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Stopped waiting; the job was not cancelled.")
            status = self._job_request(
                "GET",
                self._job_path(job_id),
                JobStatus,
                **({"timeout": remaining} if remaining is not None else {}),
            )
            if status.state in TERMINAL:
                return status
            time.sleep(
                poll_interval
                if remaining is None
                else min(poll_interval, max(0, timeout - (time.monotonic() - start)))
            )

    def submit_batch(self, sources, *, idempotency_key=None, **options) -> BatchStatus:
        request = request_options(options)
        with upload_files(sources, "files") as files:
            return self._job_request(
                "POST",
                "/v1/batches",
                BatchStatus,
                files=files,
                data={"options": json.dumps(request.model_dump(mode="json", exclude_unset=True))},
                headers={"Idempotency-Key": idempotency_key} if idempotency_key else {},
            )

    def get_batch(self, batch_id: str, *, cursor=None, limit=50) -> BatchStatus:
        return self._job_request(
            "GET",
            self._job_path(batch_id, batch=True),
            BatchStatus,
            params={"cursor": cursor or 0, "limit": limit},
        )

    def cancel_batch(self, batch_id: str) -> BatchStatus:
        return self._job_request("POST", self._job_path(batch_id, batch=True) + "/cancel", BatchStatus)

    @staticmethod
    def _job_path(identifier, batch=False):
        if (
            not isinstance(identifier, str)
            or not identifier
            or any(c not in "0123456789abcdef" for c in identifier)
        ):
            raise ValueError("Invalid job/batch identifier")
        return ("/v1/batches/" if batch else "/v1/jobs/") + identifier

    @staticmethod
    def _poll_options(interval, timeout):
        if (
            not math.isfinite(interval)
            or interval <= 0
            or (timeout is not None and (not math.isfinite(timeout) or timeout <= 0))
        ):
            raise ValueError("Polling interval and timeout must be finite positive numbers")

    async def asubmit_job(self, source, **options) -> JobStatus:
        return await asyncio.to_thread(self.submit_job, source, **options)

    async def aget_job(self, job_id) -> JobStatus:
        return await asyncio.to_thread(self.get_job, job_id)

    async def aget_job_result(self, job_id, **options) -> ParseResult:
        return await asyncio.to_thread(self.get_job_result, job_id, **options)

    async def acancel_job(self, job_id) -> JobStatus:
        return await asyncio.to_thread(self.cancel_job, job_id)

    async def await_for_job(self, job_id, *, poll_interval=1.0, timeout=None) -> JobStatus:
        self._poll_options(poll_interval, timeout)

        async def wait():
            while True:
                status = await self.aget_job(job_id)
                if status.state in TERMINAL:
                    return status
                await asyncio.sleep(poll_interval)

        return await asyncio.wait_for(wait(), timeout)

    async def asubmit_batch(self, sources, **options) -> BatchStatus:
        return await asyncio.to_thread(self.submit_batch, sources, **options)

    async def aget_batch(self, batch_id, **options) -> BatchStatus:
        return await asyncio.to_thread(self.get_batch, batch_id, **options)

    async def acancel_batch(self, batch_id) -> BatchStatus:
        return await asyncio.to_thread(self.cancel_batch, batch_id)
