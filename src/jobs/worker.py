from __future__ import annotations

import logging
import threading
import time

from trueparse.contracts.jobs import JobError
from trueparse.contracts.request import ParseRequest
from trueparse.jobs.store import JobStore
from trueparse.runtime.task import ParseTask

logger = logging.getLogger("trueparse.jobs")


class JobWorker:
    def __init__(self, store: JobStore):
        self.store = store
        self.active: dict[str, tuple[str, ParseTask]] = {}
        self.stop = threading.Event()
        self.last_maintenance = 0.0
        self.heartbeat_stop = threading.Event()
        self.lost: set[str] = set()
        self.lock = threading.Lock()
        self.heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.heartbeat_thread.start()

    def _heartbeat(self):
        while not self.heartbeat_stop.wait(self.store.settings.heartbeat_seconds):
            with self.lock:
                tasks = list(self.active.items())
            for identifier, (token, task) in tasks:
                if self.heartbeat_stop.is_set():
                    break
                try:
                    owned = self.store.heartbeat(identifier, token, task.progress)
                except Exception:
                    logger.exception("Heartbeat failed for job %s; stopping its execution.", identifier)
                    owned = False
                if not owned:
                    with self.lock:
                        if self.active.get(identifier) == (token, task):
                            self.lost.add(identifier)

    def tick(self):
        now = time.monotonic()
        if now - self.last_maintenance >= self.store.settings.heartbeat_seconds:
            self.store.maintain()
            self.last_maintenance = now
        for identifier, (token, task) in list(self.active.items()):
            if identifier in self.lost:
                task.close()
                self.store.interrupt(identifier, token)
                with self.lock:
                    self.active.pop(identifier, None)
                    self.lost.discard(identifier)
            elif task.poll():
                self.store.heartbeat(identifier, token, task.progress)
                self.store.complete(identifier, token, result=task.result, error=task.error)
                with self.lock:
                    del self.active[identifier]
        while not self.stop.is_set() and len(self.active) < self.store.settings.workers:
            job = self.store.claim()
            if job is None:
                break
            try:
                task = ParseTask(
                    self.store.storage.path("inputs", job["id"]),
                    job["filename"],
                    ParseRequest.model_validate_json(job["options"]),
                )
                with self.lock:
                    self.active[job["id"]] = (job["token"], task)
            except Exception as exc:
                logger.error(
                    "Could not start parsing process for job %s (%s).", job["id"], type(exc).__name__
                )
                self.store.complete(
                    job["id"],
                    job["token"],
                    error=JobError(code="WORKER_UNAVAILABLE", message="Could not start worker."),
                )

    def close(self):
        self.stop.set()
        for identifier, (token, task) in self.active.items():
            task.close()
            try:
                self.store.interrupt(identifier, token)
            except Exception:
                logger.exception("Could not release job %s; recovery will use lease expiry.", identifier)
        self.active.clear()
        self.heartbeat_stop.set()
        self.heartbeat_thread.join(timeout=35)
        try:
            self.store.maintain(force=True)
        except Exception:
            logger.exception("Job store maintenance failed during worker shutdown.")

    def run(self):
        try:
            while not self.stop.is_set():
                self.tick()
                self.stop.wait(0.05)
        except Exception:
            logger.exception("Job scheduler stopped unexpectedly.")
            raise
        finally:
            self.close()
