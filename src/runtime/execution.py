from __future__ import annotations

import multiprocessing
import threading
import time
from io import BytesIO
from typing import Any

from trueparse.chunking.tokenization import token_counter
from trueparse.config.options import (
    JsonOptions,
    OutputConfig,
    ParseOptions,
)
from trueparse.contracts.request import ParseRequest
from trueparse.exceptions import PDFEngineError
from trueparse.io.sources import Source, _source_bytes
from trueparse.models.enums import (
    ErrorCode,
    OutputFormat,
)
from trueparse.sdk.result import ParseResult

_PARSE_LOCK = threading.RLock()


def _parse_local(content: bytes, filename: str, request: ParseRequest) -> ParseResult:
    # PyMuPDF operations within a process are serialized. HTTP requests with a
    # deadline run in separate processes and do not share this lock.
    with _PARSE_LOCK:
        try:
            return _parse_unlocked(content, filename, request)
        except PDFEngineError:
            raise
        except Exception as exc:
            raise PDFEngineError(ErrorCode.PDF_PARSE_ERROR, "PDF processing failed.") from exc


def _parse_unlocked(
    content: bytes, filename: str, request: ParseRequest, progress_callback=None
) -> ParseResult:
    from trueparse.pipeline.runner import PDFParser

    if OutputFormat.CHUNKS in request.formats:
        token_counter(request.chunk_tokenizer)

    projection = JsonOptions(**request.json_options.model_dump(), images=request.images)
    options = ParseOptions(
        strict=request.strict,
        limits=request.limits,
        profile=request.profile,
        ocr=request.ocr,
        password=request.password,
        max_pages=request.max_pages,
        timeout_seconds=None,
        output_config=OutputConfig(formats=request.formats, json_options=projection, save_to_disk=False),
        chunk_strategy=request.chunk_strategy,
        chunk_max_tokens=request.chunk_size,
        chunk_overlap_tokens=request.chunk_overlap,
        chunk_tokenizer=request.chunk_tokenizer,
    )
    parser = PDFParser(options)
    return parser.parse_to_response(
        BytesIO(content), original_filename=filename, progress_callback=progress_callback
    )


def _worker(connection, content: bytes, filename: str, payload: dict[str, Any]) -> None:
    # Imported by name under spawn: safe in Windows and in threaded web servers.
    try:
        connection.send((True, _parse_local(content, filename, ParseRequest.model_validate(payload))))
    except Exception as exc:
        connection.send(
            (
                False,
                exc
                if isinstance(exc, PDFEngineError)
                else PDFEngineError(ErrorCode.PDF_PARSE_ERROR, str(exc)),
            )
        )
    finally:
        connection.close()


def _parse_request(source: Source, request: ParseRequest, filename: str | None = None) -> ParseResult:
    content, name = _source_bytes(source, filename)
    if request.timeout_seconds is None:
        return _parse_local(content, name, request)
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(writer, content, name, request.model_dump(mode="json")))
    started = time.monotonic()
    try:
        process.start()
        writer.close()
        remaining = max(0.0, request.timeout_seconds - (time.monotonic() - started))
        if not reader.poll(remaining):
            raise PDFEngineError(
                ErrorCode.PARSE_TIMEOUT,
                f"Parsing exceeded the {request.timeout_seconds:g} second limit.",
                retryable=True,
            )
        try:
            success, value = reader.recv()
        except (EOFError, OSError) as exc:
            raise PDFEngineError(
                ErrorCode.PDF_PARSE_ERROR, "The PDF worker exited without returning a result."
            ) from exc
        if not success:
            raise value
        duration_ms = int((time.monotonic() - started) * 1000)
        delta = len(str(duration_ms)) - len(str(value.metrics["duration_ms"]))
        if value._response_bytes + delta > request.limits.max_response_bytes:
            raise PDFEngineError(ErrorCode.PDF_RESOURCE_LIMIT, "Response exceeds max_response_bytes.")
        value.metrics["duration_ms"] = duration_ms
        value._response_bytes += delta
        return value
    finally:
        reader.close()
        writer.close()
        if process.pid is not None:
            process.join(timeout=0.5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()
