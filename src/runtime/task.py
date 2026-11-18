from __future__ import annotations

import multiprocessing
import os
import queue
import threading
import time

from trueparse.contracts.jobs import JobError, JobProgress
from trueparse.contracts.request import ParseRequest
from trueparse.exceptions import PDFEngineError
from trueparse.io.sources import _source_bytes
from trueparse.runtime.execution import _parse_unlocked
from trueparse.sdk.result import ParseResult


def _child(connection, source, filename, payload):
    parent = multiprocessing.parent_process()

    def watch_parent():
        while parent is not None and parent.is_alive():
            time.sleep(0.5)
        os._exit(1)

    threading.Thread(target=watch_parent, daemon=True).start()
    last = 0.0

    def progress(current, total, stage):
        nonlocal last
        now = time.monotonic()
        if now - last >= 0.25 or current == total:
            connection.send(("progress", JobProgress(current_page=current, total_pages=total, stage=stage)))
            last = now

    try:
        content, name = _source_bytes(source, filename)
        result = _parse_unlocked(content, name, ParseRequest.model_validate(payload), progress)
        connection.send(("result", result))
    except PDFEngineError as exc:
        connection.send(
            (
                "error",
                JobError(
                    code=exc.code.value,
                    message=exc.message,
                    retryable=exc.retryable,
                    details=exc.details,
                    page=exc.page,
                    element_id=exc.element_id,
                ),
            )
        )
    except Exception:
        connection.send(("error", JobError(code="PDF_PARSE_ERROR", message="PDF processing failed.")))
    finally:
        connection.close()


class ParseTask:
    """One owned process; a reader thread keeps deadline checks out of pipe reads."""

    def __init__(self, source, filename, request: ParseRequest):
        context = multiprocessing.get_context("spawn")
        self.reader, writer = context.Pipe(duplex=False)
        self.process = context.Process(
            target=_child, args=(writer, source, filename, request.model_dump(mode="json")), daemon=True
        )
        self.started = time.monotonic()
        self.timeout = request.timeout_seconds
        self.messages: queue.Queue = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.progress = JobProgress()
        self.result: ParseResult | None = None
        self.error: JobError | None = None
        self.closed = False
        self.done = False
        try:
            self.process.start()
        except BaseException:
            self.reader.close()
            writer.close()
            self.process.close()
            raise
        writer.close()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while not self.stopped.is_set():
                message = self.reader.recv()
                while not self.stopped.is_set():
                    try:
                        self.messages.put(message, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if message[0] in ("result", "error"):
                    return
        except (EOFError, OSError):
            while not self.stopped.is_set():
                try:
                    self.messages.put(
                        (
                            "error",
                            JobError(code="WORKER_INTERRUPTED", message="Worker exited without a result."),
                        ),
                        timeout=0.1,
                    )
                    return
                except queue.Full:
                    pass

    def poll(self):
        if self.done:
            return True
        if self.timeout is not None and time.monotonic() - self.started >= self.timeout:
            self.error = JobError(code="PARSE_TIMEOUT", message="Parsing deadline exceeded.", retryable=True)
            self.done = True
            self.close()
            return True
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                return False
            if kind == "progress":
                self.progress = value
            else:
                if kind == "result":
                    self.result = value
                else:
                    self.error = value
                self.done = True
                self.close()
                return True

    def close(self):
        self.stopped.set()
        if self.closed:
            return
        self.process.join(timeout=0.05)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join()
        self.thread.join(timeout=2)
        self.reader.close()
        self.process.close()
        self.closed = True
