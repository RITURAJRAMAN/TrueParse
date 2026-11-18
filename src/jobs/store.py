from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from trueparse.contracts.jobs import TERMINAL, BatchState, BatchStatus, JobError, JobProgress, JobStatus
from trueparse.contracts.request import ParseRequest
from trueparse.io.job_storage import JobStorage
from trueparse.jobs.settings import JobSettings


class JobServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


class JobStore:
    def __init__(self, settings: JobSettings, clock=time.time):
        self.settings, self.clock = settings, clock
        self.storage = JobStorage(settings.root)
        self.db = self.storage.root / "jobs.sqlite3"
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported jobs database version: {version}")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stages (id TEXT PRIMARY KEY, bytes INTEGER NOT NULL, lease REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS submissions (
                    key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, batch_id TEXT, input_index INTEGER NOT NULL, filename TEXT NOT NULL,
                    state TEXT NOT NULL, created REAL NOT NULL, started REAL, finished REAL, expires REAL,
                    attempt INTEGER NOT NULL DEFAULT 0, token TEXT, lease REAL,
                    options TEXT, input_bytes INTEGER NOT NULL, result_bytes INTEGER NOT NULL DEFAULT 0,
                    reserved INTEGER NOT NULL DEFAULT 0, progress TEXT NOT NULL DEFAULT '{}',
                    document_id TEXT, error TEXT);
                CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, created);
                CREATE INDEX IF NOT EXISTS jobs_batch ON jobs(batch_id, input_index);
                PRAGMA user_version=1;
            """)
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('fingerprint_key', ?)", (secrets.token_hex(32),)
            )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _used(self, db):
        return (
            db.execute("SELECT COALESCE(SUM(input_bytes+result_bytes+reserved),0) FROM jobs").fetchone()[0]
            + db.execute("SELECT COALESCE(SUM(bytes),0) FROM stages").fetchone()[0]
        )

    def begin_stage(self) -> str:
        identifier = secrets.token_hex(16)
        with self.transaction() as db:
            reservation = self.settings.max_upload_bytes
            if self._used(db) + reservation > self.settings.disk_quota_bytes:
                raise JobServiceError(507, "JOB_STORAGE_FULL", "Insufficient storage for upload reservation.")
            db.execute(
                "INSERT INTO stages VALUES (?, ?, ?)",
                (identifier, reservation, self.clock() + self.settings.staging_seconds),
            )
            self.storage.path("staging", identifier).mkdir(mode=0o700)
        return identifier

    def touch_stage(self, identifier):
        with self.transaction() as db:
            if not db.execute(
                "UPDATE stages SET lease=? WHERE id=? AND lease>?",
                (self.clock() + self.settings.staging_seconds, identifier, self.clock()),
            ).rowcount:
                raise JobServiceError(409, "UPLOAD_EXPIRED", "Upload reservation expired.")

    def discard_stage(self, identifier):
        with self.transaction() as db:
            self.storage.remove("staging", identifier)
            db.execute("DELETE FROM stages WHERE id=?", (identifier,))

    def admit(
        self, stage: str, files: list[tuple[str, Path]], options: ParseRequest, *, batch=False, key=None
    ):
        if not files or len(files) > (self.settings.max_batch_files if batch else 1):
            raise JobServiceError(422, "INVALID_OPTIONS", "Invalid number of input files.")
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 128):
            raise JobServiceError(422, "INVALID_OPTIONS", "Idempotency-Key must contain 1 to 128 characters.")
        encoded = options.model_dump_json()
        entries = []
        for name, path in files:
            if path.is_symlink() or not path.resolve().is_relative_to(self.storage.path("staging", stage)):
                raise ValueError("Input is outside staging")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            entries.append(
                (
                    Path(name.replace("\\", "/")).name or "document.pdf",
                    path,
                    path.stat().st_size,
                    digest.hexdigest(),
                )
            )
        if sum(entry[2] for entry in entries) > self.settings.max_upload_bytes or any(
            entry[2] > 200 * 1024**2 for entry in entries
        ):
            raise JobServiceError(413, "UPLOAD_TOO_LARGE", "Upload exceeds input limits.")
        with self.transaction() as db:
            reservation = db.execute(
                "SELECT * FROM stages WHERE id=? AND lease>?", (stage, self.clock())
            ).fetchone()
            if not reservation:
                raise JobServiceError(409, "UPLOAD_EXPIRED", "Upload reservation expired.")
            secret = db.execute("SELECT value FROM metadata WHERE key='fingerprint_key'").fetchone()[0]
            fingerprint = hmac.new(
                secret.encode(),
                json.dumps(
                    [batch, json.loads(encoded), [(e[0], e[3]) for e in entries]], sort_keys=True
                ).encode(),
                hashlib.sha256,
            ).hexdigest()
            if key:
                previous = db.execute("SELECT * FROM submissions WHERE key=?", (key,)).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise JobServiceError(
                            409, "IDEMPOTENCY_CONFLICT", "Key was used for different input or options."
                        )
                    return previous["target"]
            count = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','cancelling')"
            ).fetchone()[0]
            if count + len(entries) > self.settings.queue_capacity:
                raise JobServiceError(429, "JOB_QUEUE_FULL", "Job queue capacity exceeded.")
            batch_id = secrets.token_hex(16) if batch else None
            now = self.clock()
            if batch_id:
                db.execute("INSERT INTO batches VALUES (?,?)", (batch_id, now))
            identifiers = []
            moved = []
            try:
                for index, (name, path, size, _) in enumerate(entries):
                    identifier = secrets.token_hex(16)
                    target = self.storage.path("inputs", identifier)
                    os.replace(path, target)
                    moved.append((target, path))
                    db.execute(
                        "INSERT INTO jobs (id,batch_id,input_index,filename,state,created,options,input_bytes) VALUES (?,?,?,?, 'queued',?,?,?)",
                        (identifier, batch_id, index, name, now, encoded, size),
                    )
                    identifiers.append(identifier)
                target_id = batch_id or identifiers[0]
                if key:
                    db.execute(
                        "INSERT INTO submissions VALUES (?,?,?,?)",
                        (key, fingerprint, "batch" if batch else "job", target_id),
                    )
                db.execute("DELETE FROM stages WHERE id=?", (stage,))
            except BaseException:
                for target, original in reversed(moved):
                    os.replace(target, original)
                raise
        return target_id

    def _row(self, db, identifier):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise JobServiceError(404, "JOB_NOT_FOUND", "Unknown job.")
        return row

    def _status(self, row) -> JobStatus:
        identifier = row["id"]
        return JobStatus(
            job_id=identifier,
            batch_id=row["batch_id"],
            input_index=row["input_index"],
            filename=row["filename"],
            state=row["state"],
            created_at=row["created"],
            started_at=row["started"],
            finished_at=row["finished"],
            expires_at=row["expires"],
            attempt=row["attempt"],
            max_attempts=self.settings.max_attempts,
            progress=JobProgress.model_validate_json(row["progress"]),
            document_id=row["document_id"],
            error=JobError.model_validate_json(row["error"]) if row["error"] else None,
            status_url=f"/v1/jobs/{identifier}",
            result_url=f"/v1/jobs/{identifier}/result",
        )

    def get(self, identifier) -> JobStatus:
        with self.connection() as db:
            return self._status(self._row(db, identifier))

    def batch(self, identifier, cursor=0, limit=50) -> BatchStatus:
        if cursor < 0 or not 1 <= limit <= 100:
            raise JobServiceError(422, "INVALID_OPTIONS", "Invalid batch pagination.")
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM batches WHERE id=?", (identifier,)).fetchone():
                raise JobServiceError(404, "BATCH_NOT_FOUND", "Unknown batch.")
            counts = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT state,COUNT(*) FROM jobs WHERE batch_id=? GROUP BY state", (identifier,)
                )
            }
            rows = db.execute(
                "SELECT * FROM jobs WHERE batch_id=? AND input_index>=? ORDER BY input_index LIMIT ?",
                (identifier, cursor, limit + 1),
            ).fetchall()
            states = set(counts)
            state = (
                "running"
                if states & {"running", "cancelling"} or ("queued" in states and len(states) > 1)
                else next(iter(states))
                if len(states) == 1
                else "mixed"
            )
            return BatchStatus(
                batch_id=identifier,
                state=cast(BatchState, state),
                total=sum(counts.values()),
                counts=counts,
                jobs=[self._status(row) for row in rows[:limit]],
                next_cursor=rows[limit]["input_index"] if len(rows) > limit else None,
                status_url=f"/v1/batches/{identifier}",
            )

    def _finish(self, db, row, state, error=None, result_bytes=0, document_id=None):
        now = self.clock()
        db.execute(
            "UPDATE jobs SET state=?,finished=?,expires=?,options=NULL,reserved=0,token=NULL,lease=NULL,error=?,result_bytes=?,document_id=? WHERE id=?",
            (
                state,
                now,
                now + self.settings.retention_seconds,
                error.model_dump_json() if error else None,
                result_bytes,
                document_id,
                row["id"],
            ),
        )

        progress = json.loads(row["progress"])
        progress["stage"] = state
        db.execute("UPDATE jobs SET progress=? WHERE id=?", (json.dumps(progress), row["id"]))

    def cancel(self, identifier):
        with self.transaction() as db:
            row = self._row(db, identifier)
            if row["state"] == "queued":
                self._finish(db, row, "cancelled")
            elif row["state"] == "running":
                db.execute("UPDATE jobs SET state='cancelling' WHERE id=?", (identifier,))
        self.clean_inputs()
        return self.get(identifier)

    def cancel_batch(self, identifier):
        self.batch(identifier)
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM jobs WHERE batch_id=?", (identifier,)).fetchall():
                if row["state"] == "queued":
                    self._finish(db, row, "cancelled")
                elif row["state"] == "running":
                    db.execute("UPDATE jobs SET state='cancelling' WHERE id=?", (row["id"],))
        self.clean_inputs()
        return self.batch(identifier)

    def claim(self):
        with self.transaction() as db:
            # Global concurrency cap across schedulers sharing the same store.
            if (
                db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('running','cancelling')").fetchone()[0]
                >= self.settings.workers
            ):
                return None
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row is None:
                return None
            options = ParseRequest.model_validate_json(row["options"])
            reserve = options.limits.max_response_bytes
            if self._used(db) + reserve > self.settings.disk_quota_bytes:
                self._finish(
                    db,
                    row,
                    "failed",
                    JobError(code="JOB_STORAGE_FULL", message="Insufficient result reservation."),
                )
                return None
            token = secrets.token_hex(16)
            now = self.clock()
            db.execute(
                "UPDATE jobs SET state='running',token=?,lease=?,started=?,attempt=attempt+1,reserved=?,progress='{}' WHERE id=?",
                (token, now + self.settings.lease_seconds, now, reserve, row["id"]),
            )
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def heartbeat(self, identifier, token, progress=None):
        with self.transaction() as db:
            row = self._row(db, identifier)
            if (
                row["token"] != token
                or row["state"] not in ("running", "cancelling")
                or row["lease"] <= self.clock()
            ):
                return False
            db.execute(
                "UPDATE jobs SET lease=?,progress=? WHERE id=?",
                (
                    self.clock() + self.settings.lease_seconds,
                    progress.model_dump_json() if progress else row["progress"],
                    identifier,
                ),
            )
            return row["state"] == "running"

    def complete(self, identifier, token, result=None, error=None):
        with self.transaction() as db:
            row = self._row(db, identifier)
            if (
                row["token"] != token
                or row["state"] not in ("running", "cancelling")
                or row["lease"] <= self.clock()
            ):
                return False
            if row["state"] == "cancelling":
                self._finish(db, row, "cancelled")
            elif error is not None:
                self._finish(db, row, "failed", error)
            else:
                target = self.storage.path("results", identifier)
                temporary = self.storage.path("results", identifier + "_tmp")
                size = 0
                try:
                    with temporary.open("wb") as stream:
                        for text in json.JSONEncoder(
                            ensure_ascii=False, separators=(",", ":"), allow_nan=False
                        ).iterencode(result.model_dump(mode="json")):
                            content = text.encode("utf-8")
                            size += len(content)
                            if size > row["reserved"]:
                                raise ValueError("Result exceeded reserved bytes")
                            stream.write(content)
                        self.storage.sync(stream)
                    os.replace(temporary, target)
                    self._finish(db, row, "succeeded", result_bytes=size, document_id=result.document_id)
                except (OSError, ValueError):
                    temporary.unlink(missing_ok=True)
                    target.unlink(missing_ok=True)
                    self._finish(
                        db,
                        row,
                        "failed",
                        JobError(code="ASSET_STORAGE_ERROR", message="Could not publish job result."),
                    )
        self.clean_inputs()
        return True

    def clean_inputs(self):
        with self.transaction() as db:
            for row in db.execute(
                "SELECT id FROM jobs WHERE state IN ('succeeded','failed','cancelled','expired') AND input_bytes>0"
            ).fetchall():
                self.storage.remove("inputs", row["id"])
                db.execute("UPDATE jobs SET input_bytes=0 WHERE id=?", (row["id"],))

    def result(self, identifier):
        with self.transaction() as db:
            row = self._row(db, identifier)
            if row["state"] == "expired" or (row["expires"] is not None and row["expires"] <= self.clock()):
                raise JobServiceError(410, "JOB_EXPIRED", "Job result expired.")
            if row["state"] != "succeeded":
                raise JobServiceError(409, "JOB_NOT_READY", "Job has no successful result.")
            try:
                return self.storage.path("results", identifier).read_bytes()
            except OSError as exc:
                raise JobServiceError(500, "RESULT_UNAVAILABLE", "Stored result is unavailable.") from exc

    def interrupt(self, identifier, token):
        with self.transaction() as db:
            db.execute(
                "UPDATE jobs SET lease=0 WHERE id=? AND token=? AND state IN ('running','cancelling')",
                (identifier, token),
            )

    def maintain(self, *, force=False):
        now = self.clock()
        self.clean_inputs()
        with self.transaction() as db:
            previous = db.execute("SELECT value FROM metadata WHERE key='maintenance_at'").fetchone()
            if not force and previous and now - float(previous[0]) < min(5, self.settings.heartbeat_seconds):
                return
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('maintenance_at',?)", (str(now),))
            for row in db.execute(
                "SELECT * FROM jobs WHERE state IN ('running','cancelling') AND lease<=?", (now,)
            ).fetchall():
                if row["state"] == "cancelling":
                    self._finish(db, row, "cancelled")
                elif row["attempt"] >= self.settings.max_attempts:
                    self._finish(
                        db,
                        row,
                        "failed",
                        JobError(
                            code="WORKER_INTERRUPTED", message="Worker lease expired; attempts exhausted."
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE jobs SET state='queued',token=NULL,lease=NULL,reserved=0,progress='{}' WHERE id=?",
                        (row["id"],),
                    )
            for row in db.execute(
                "SELECT * FROM jobs WHERE state IN ('succeeded','failed','cancelled') AND expires<=?", (now,)
            ).fetchall():
                self.storage.remove("results", row["id"])
                db.execute(
                    "UPDATE jobs SET state='expired',result_bytes=0,error=NULL WHERE id=?", (row["id"],)
                )
            db.execute(
                "DELETE FROM submissions WHERE kind='job' AND target IN (SELECT id FROM jobs WHERE state='expired' AND expires<=?)",
                (now - self.settings.tombstone_seconds,),
            )
            db.execute(
                "DELETE FROM jobs WHERE batch_id IS NULL AND state='expired' AND expires<=?",
                (now - self.settings.tombstone_seconds,),
            )
            for batch in db.execute("SELECT id FROM batches").fetchall():
                if not db.execute(
                    "SELECT 1 FROM jobs WHERE batch_id=? AND (state!='expired' OR expires>?)",
                    (batch["id"], now - self.settings.tombstone_seconds),
                ).fetchone():
                    db.execute("DELETE FROM submissions WHERE kind='batch' AND target=?", (batch["id"],))
                    db.execute("DELETE FROM jobs WHERE batch_id=?", (batch["id"],))
                    db.execute("DELETE FROM batches WHERE id=?", (batch["id"],))
            for stage in db.execute("SELECT id FROM stages WHERE lease<=?", (now,)).fetchall():
                self.storage.remove("staging", stage["id"])
                db.execute("DELETE FROM stages WHERE id=?", (stage["id"],))
            # Reconcile files left by a crash between filesystem operations and commit.
            for kind in ("inputs", "results", "staging"):
                for path in (self.storage.root / kind).iterdir():
                    table = "stages" if kind == "staging" else "jobs"
                    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (path.name,)).fetchone()
                    needed = row is not None and (
                        kind == "staging"
                        or (kind == "inputs" and row["state"] not in TERMINAL)
                        or (kind == "results" and row["state"] == "succeeded")
                    )
                    if not needed:
                        self.storage.remove(kind, path.name)
