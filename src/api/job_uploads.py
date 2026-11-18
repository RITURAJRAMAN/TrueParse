from __future__ import annotations

import time
from pathlib import Path
from typing import Any, BinaryIO, cast

from fastapi import Request
from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool

from trueparse.api.settings import max_upload_bytes
from trueparse.jobs.store import JobServiceError, JobStore


class JobUpload:
    def __init__(self, store: JobStore, stage: str, batch: bool):
        self.store, self.stage, self.batch = store, stage, batch
        self.files: list[tuple[str, Path]] = []
        self.options: str | None = None
        self.stream: BinaryIO | None = None
        self.field = b""
        self.value = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.header_name = bytearray()
        self.header_value = bytearray()
        self.header_bytes = 0
        self.file_bytes = 0
        self.total = 0
        self.finished = False
        self.filename = "document.pdf"

    def on_part_begin(self):
        self.headers = {}
        self.header_bytes = 0
        self.value = bytearray()
        self.file_bytes = 0

    def on_header_field(self, data, start, end):
        self.header_name.extend(data[start:end])
        self._header_limit(end - start)

    def on_header_value(self, data, start, end):
        self.header_value.extend(data[start:end])
        self._header_limit(end - start)

    def _header_limit(self, size):
        self.header_bytes += size
        if self.header_bytes > 16384:
            raise JobServiceError(422, "INVALID_OPTIONS", "Multipart headers too large.")

    def on_header_end(self):
        name = bytes(self.header_name).lower()
        if name in self.headers:
            raise JobServiceError(422, "INVALID_OPTIONS", "Duplicate multipart header.")
        self.headers[name] = bytes(self.header_value)
        self.header_name.clear()
        self.header_value.clear()

    def on_headers_finished(self):
        _, options = parse_options_header(self.headers.get(b"content-disposition", b""))
        self.field = options.get(b"name", b"")
        if b"filename" in options:
            expected = b"files" if self.batch else b"file"
            cap = self.store.settings.max_batch_files if self.batch else 1
            if self.field != expected or len(self.files) >= cap:
                raise JobServiceError(422, "INVALID_OPTIONS", "Invalid file field or too many files.")
            self.filename = options[b"filename"].decode("utf-8", errors="replace")[:1024]
            path = self.store.storage.path("staging", self.stage) / str(len(self.files))
            self.stream = path.open("xb")
        elif self.field != b"options" or self.options is not None:
            raise JobServiceError(422, "INVALID_OPTIONS", "Only one options field is allowed.")

    def on_part_data(self, data, start, end):
        content = data[start:end]
        if self.stream is not None:
            self.file_bytes += len(content)
            self.total += len(content)
            if (
                self.file_bytes > min(max_upload_bytes(), 200 * 1024**2)
                or self.total > self.store.settings.max_upload_bytes
            ):
                raise JobServiceError(413, "UPLOAD_TOO_LARGE", "Upload exceeds configured limits.")
            self.stream.write(content)
        else:
            if len(self.value) + len(content) > 16384:
                raise JobServiceError(422, "INVALID_OPTIONS", "Options exceed 16 KiB.")
            self.value.extend(content)

    def on_part_end(self):
        if self.stream is not None:
            self.store.storage.sync(self.stream)
            self.stream.close()
            self.stream = None
            self.files.append(
                (self.filename, self.store.storage.path("staging", self.stage) / str(len(self.files)))
            )
        else:
            self.options = self.value.decode("utf-8")

    def on_end(self):
        self.finished = True

    def close(self):
        if self.stream is not None:
            self.stream.close()


async def receive_job_upload(request: Request, store: JobStore, stage: str, batch: bool):
    content_type, parameters = parse_options_header(request.headers.get("content-type", ""))
    boundary = parameters.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise JobServiceError(415, "INVALID_OPTIONS", "Use multipart/form-data with a boundary.")
    upload = JobUpload(store, stage, batch)
    callbacks = {
        name: getattr(upload, name)
        for name in (
            "on_part_begin",
            "on_header_field",
            "on_header_value",
            "on_header_end",
            "on_headers_finished",
            "on_part_data",
            "on_part_end",
            "on_end",
        )
    }
    parser = MultipartParser(boundary, cast(Any, callbacks))
    received = 0
    last_touch = time.monotonic()
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > store.settings.max_upload_bytes + 65536:
                raise JobServiceError(413, "UPLOAD_TOO_LARGE", "Batch transport limit exceeded.")
            if time.monotonic() - last_touch >= min(5, store.settings.staging_seconds / 3):
                await run_in_threadpool(store.touch_stage, stage)
                last_touch = time.monotonic()
            await run_in_threadpool(parser.write, chunk)
        parser.finalize()
        if not upload.finished or not upload.files:
            raise JobServiceError(422, "INVALID_OPTIONS", "Incomplete multipart upload or no files.")
        return upload.files, upload.options or "{}"
    except (ValueError, UnicodeError) as exc:
        raise JobServiceError(422, "INVALID_OPTIONS", "Invalid multipart upload.") from exc
    finally:
        upload.close()
