from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path

from pydantic import ValidationError

from trueparse.chunking.tokenization import token_counter
from trueparse.contracts.jobs import JobError, ParseOutcome
from trueparse.contracts.request import ParseRequest
from trueparse.exceptions import PDFEngineError
from trueparse.io.sources import Source, _source_bytes
from trueparse.models.enums import ErrorCode, OutputFormat
from trueparse.runtime.task import ParseTask


def _many(sources, request, max_workers, ordered, stopped):
    pending: dict[int, tuple[str, ParseTask]] = {}
    completed: dict[int, ParseOutcome] = {}
    iterator = iter(sources)
    next_index = 0
    next_yield = 0
    exhausted = False
    try:
        while not stopped.is_set():
            while not exhausted and len(pending) + len(completed) < max_workers and not stopped.is_set():
                try:
                    source = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                index = next_index
                next_index += 1
                filename = Path(source).name if isinstance(source, (str, Path)) else "document.pdf"
                try:
                    if not isinstance(source, (str, Path, bytes)):
                        source, filename = _source_bytes(source, filename)
                    pending[index] = (filename, ParseTask(source, filename, request))
                except (PDFEngineError, OSError, TypeError, ValueError) as exc:
                    completed[index] = ParseOutcome(
                        input_index=index,
                        filename=filename,
                        error=JobError(
                            code=exc.code.value if isinstance(exc, PDFEngineError) else "INVALID_PDF",
                            message=str(exc),
                        ),
                    )
            for index, (filename, task) in list(pending.items()):
                if task.poll():
                    completed[index] = ParseOutcome(
                        input_index=index, filename=filename, result=task.result, error=task.error
                    )
                    del pending[index]
            if stopped.is_set():
                break
            if ordered:
                while next_yield in completed:
                    yield completed.pop(next_yield)
                    next_yield += 1
            else:
                for index in list(completed):
                    yield completed.pop(index)
            if exhausted and not pending and not completed:
                break
            stopped.wait(0.01)
    finally:
        for _, task in pending.values():
            task.close()


def _request(options, max_workers):
    if type(max_workers) is not int or not 1 <= max_workers <= 64:
        raise ValueError("max_workers must be between 1 and 64")
    try:
        request = ParseRequest.model_validate(options)
    except ValidationError as exc:
        raise PDFEngineError(ErrorCode.INVALID_OPTIONS, "Invalid parsing options.") from exc
    if OutputFormat.CHUNKS in request.formats:
        token_counter(request.chunk_tokenizer)
    return request


def parse_many(
    sources: Iterable[Source], *, max_workers: int = 2, ordered: bool = True, **options
) -> Iterator[ParseOutcome]:
    return _many(sources, _request(options, max_workers), max_workers, ordered, threading.Event())


async def aparse_many(
    sources: Iterable[Source], *, max_workers: int = 2, ordered: bool = True, **options
) -> AsyncIterator[ParseOutcome]:
    stopped = threading.Event()
    iterator = _many(sources, _request(options, max_workers), max_workers, ordered, stopped)
    sentinel = object()
    pending = None
    try:
        while True:
            pending = asyncio.create_task(asyncio.to_thread(next, iterator, sentinel))
            value = await asyncio.shield(pending)
            pending = None
            if value is sentinel:
                break
            assert isinstance(value, ParseOutcome)
            yield value
    finally:
        stopped.set()
        if pending is not None:
            await asyncio.shield(pending)
        await asyncio.to_thread(iterator.close)
