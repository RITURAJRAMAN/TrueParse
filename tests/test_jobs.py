import asyncio
import json
import multiprocessing
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing, closing
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from trueparse import ParseError, TrueParseClient, aparse_many, get_schema, parse, parse_many
from trueparse.api.app import app
from trueparse.contracts.jobs import JobError, JobProgress, ParseOutcome
from trueparse.contracts.request import ParseRequest
from trueparse.jobs.settings import JobSettings
from trueparse.jobs.store import JobServiceError, JobStore
from trueparse.jobs.worker import JobWorker


@pytest.fixture
def store(tmp_path):
    return JobStore(
        JobSettings(root=tmp_path / "jobs", heartbeat_seconds=0.05, lease_seconds=1, max_upload_bytes=1024**2)
    )


def admit(store, contents, *, batch=False, key=None, **options):
    stage = store.begin_stage()
    try:
        files = []
        for index, content in enumerate(contents):
            path = store.storage.path("staging", stage) / str(index)
            path.write_bytes(content)
            files.append(("same.pdf", path))
        request = ParseRequest(formats=["json", "text"], ocr="never", timeout_seconds=20, **options)
        return store.admit(stage, files, request, batch=batch, key=key)
    finally:
        store.discard_stage(stage)


def drive(worker, identifiers, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        worker.tick()
        jobs = [worker.store.get(identifier) for identifier in identifiers]
        if all(job.state in ("succeeded", "failed", "cancelled") for job in jobs):
            return jobs
        time.sleep(0.02)
    pytest.fail("Worker did not reach terminal state")


def test_outcome_requires_exactly_one_value():
    with pytest.raises(ValueError):
        ParseOutcome(input_index=0, filename="x.pdf")
    assert (
        ParseOutcome(input_index=0, filename="x.pdf", error=JobError(code="X", message="x")).error.code == "X"
    )


def test_disabled_jobs_do_not_create_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUEPARSE_JOBS_ENABLED", "false")
    root = tmp_path / "disabled"
    monkeypatch.setenv("TRUEPARSE_JOBS_ROOT", str(root))
    with TestClient(app) as client:
        assert client.post("/v1/jobs", files={"file": ("x.pdf", b"x")}).status_code == 503
        assert client.get("/health").status_code == 200
    assert not root.exists()


def wait_for_api_job(client, identifier, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        response = client.get(f"/v1/jobs/{identifier}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.02)
    pytest.fail("Managed API worker did not finish")


def test_api_processes_jobs_and_batches_by_default(tmp_path, monkeypatch, sample_pdf_path):
    monkeypatch.delenv("TRUEPARSE_JOBS_ENABLED", raising=False)
    monkeypatch.setenv("TRUEPARSE_JOBS_ROOT", str(tmp_path / "managed"))
    before = {p.pid for p in multiprocessing.active_children()}
    with TestClient(app) as client:
        thread = app.state.job_thread
        assert thread.is_alive()
        response = client.post(
            "/v1/jobs",
            files={"file": ("report.pdf", sample_pdf_path.read_bytes())},
            data={"options": '{"formats":["text"],"ocr":"never"}'},
        )
        assert response.status_code == 202, response.text
        job = wait_for_api_job(client, response.json()["job_id"])
        assert job["state"] == "succeeded"
        assert "Revenue" in client.get(job["result_url"]).json()["data"]["text"]
        response = client.post(
            "/v1/batches",
            files=[("files", ("same.pdf", sample_pdf_path.read_bytes())), ("files", ("same.pdf", b"bad"))],
            data={"options": '{"formats":["text"],"ocr":"never"}'},
        )
        assert response.status_code == 202, response.text
        batch = response.json()
        outcomes = [wait_for_api_job(client, job["job_id"]) for job in batch["jobs"]]
        assert [job["state"] for job in outcomes] == ["succeeded", "failed"]
        assert client.get(batch["status_url"]).json()["state"] == "mixed"
    assert not thread.is_alive()
    assert app.state.job_worker is None
    assert {p.pid for p in multiprocessing.active_children()} <= before


def _blocked_parse(_connection, _source, _filename, _payload):
    time.sleep(60)


def test_api_shutdown_cleans_processes_and_restart_recovers(tmp_path, monkeypatch, sample_pdf_path):
    from trueparse.runtime.task import ParseTask

    monkeypatch.setenv("TRUEPARSE_JOBS_ROOT", str(tmp_path / "recovery"))
    monkeypatch.delenv("TRUEPARSE_JOBS_ENABLED", raising=False)
    before = {p.pid for p in multiprocessing.active_children()}
    with monkeypatch.context() as patch:
        patch.setattr(JobWorker, "tick", lambda self: None)
        patch.setattr("trueparse.runtime.task._child", _blocked_parse)
        with TestClient(app):
            worker = app.state.job_worker
            identifier = admit(worker.store, [sample_pdf_path.read_bytes()])
            claim = worker.store.claim()
            task = ParseTask(
                worker.store.storage.path("inputs", identifier),
                claim["filename"],
                ParseRequest.model_validate_json(claim["options"]),
            )
            with worker.lock:
                worker.active[identifier] = (claim["token"], task)
        assert task.closed
        assert worker.store.get(identifier).state == "queued"
    assert {p.pid for p in multiprocessing.active_children()} <= before
    with TestClient(app) as client:
        job = wait_for_api_job(client, identifier)
        assert job["state"] == "succeeded"
        assert job["attempt"] == 2


def test_stopped_managed_worker_rejects_submissions(api):
    worker = api.app.state.job_worker
    worker.stop.set()
    api.app.state.job_thread.join(timeout=5)
    response = api.post("/v1/jobs", files={"file": ("x.pdf", b"x")})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WORKER_UNAVAILABLE"
    assert api.get("/health").status_code == 503


def test_admission_idempotency_and_atomic_capacity(store):
    first = admit(store, [b"pdf"], key="same")
    assert admit(store, [b"pdf"], key="same") == first
    with pytest.raises(JobServiceError) as error:
        admit(store, [b"changed"], key="same")
    assert error.value.status == 409
    store.settings.queue_capacity = 2
    with pytest.raises(JobServiceError) as error:
        admit(store, [b"a", b"b"], batch=True)
    assert error.value.status == 429
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
    assert list(store.storage.path("inputs", first).parent.iterdir()) == [store.storage.path("inputs", first)]


@pytest.mark.parametrize("max_attempts", [1, 2])
def test_heartbeat_fault_logs_and_recovers_as_interruption(store, monkeypatch, caplog, max_attempts):
    store.settings.max_attempts = max_attempts
    identifier = admit(store, [b"pdf"])
    claim = store.claim()
    task = Mock(progress=JobProgress())

    def heartbeat_fault(*args):
        worker.heartbeat_stop.set()
        raise sqlite3.OperationalError("injected heartbeat fault")

    monkeypatch.setattr(store, "heartbeat", heartbeat_fault)
    worker = JobWorker(store)
    with worker.lock:
        worker.active[identifier] = (claim["token"], task)
    try:
        worker.heartbeat_thread.join(timeout=2)
        assert not worker.heartbeat_thread.is_alive()
        worker.stop.set()
        worker.tick()
        store.maintain(force=True)
        task.close.assert_called_once()
        job = store.get(identifier)
        assert job.state == ("failed" if max_attempts == 1 else "queued")
        assert job.error is None or job.error.code == "WORKER_INTERRUPTED"
        assert "injected heartbeat fault" in caplog.text
        assert identifier in caplog.text
    finally:
        worker.close()


def test_worker_shutdown_closes_all_tasks_when_store_fails(store, monkeypatch, caplog):
    identifiers = [admit(store, [b"pdf"]) for _ in range(2)]
    claims = [store.claim(), store.claim()]
    tasks = [Mock(progress=JobProgress()) for _ in claims]
    worker = JobWorker(store)
    with worker.lock:
        worker.active.update(
            {
                identifier: (claim["token"], task)
                for identifier, claim, task in zip(identifiers, claims, tasks, strict=True)
            }
        )

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("injected shutdown fault")

    monkeypatch.setattr(store, "interrupt", unavailable)
    monkeypatch.setattr(store, "maintain", unavailable)
    try:
        worker.close()
        assert all(task.close.called for task in tasks)
        assert not worker.active
        assert not worker.heartbeat_thread.is_alive()
        assert "injected shutdown fault" in caplog.text
    finally:
        worker.heartbeat_stop.set()
        worker.heartbeat_thread.join(timeout=2)


def test_stale_heartbeat_does_not_stop_replacement_attempt(store, monkeypatch):
    identifier = admit(store, [b"pdf"])
    first = store.claim()
    original = Mock(progress=JobProgress())
    replacement = Mock(progress=JobProgress())

    def stale_heartbeat(*args):
        store.interrupt(identifier, first["token"])
        store.maintain(force=True)
        second = store.claim()
        with worker.lock:
            worker.active[identifier] = (second["token"], replacement)
        worker.heartbeat_stop.set()
        return False

    monkeypatch.setattr(store, "heartbeat", stale_heartbeat)
    worker = JobWorker(store)
    with worker.lock:
        worker.active[identifier] = (first["token"], original)
    try:
        worker.heartbeat_thread.join(timeout=2)
        assert not worker.heartbeat_thread.is_alive()
        assert identifier not in worker.lost
    finally:
        worker.close()


def test_disk_reservations_and_publication_capacity(tmp_path):
    store = JobStore(JobSettings(root=tmp_path, max_upload_bytes=100, disk_quota_bytes=150))
    stage = store.begin_stage()
    with pytest.raises(JobServiceError) as error:
        store.begin_stage()
    assert error.value.status == 507
    store.discard_stage(stage)
    identifier = admit(store, [b"a"])
    assert store.claim() is None
    assert store.get(identifier).error.code == "JOB_STORAGE_FULL"


def test_atomic_claim_global_capacity_and_fencing(store):
    identifiers = [admit(store, [b"a"]) for _ in range(4)]
    with ThreadPoolExecutor(4) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(4)))
    assert len([claim for claim in claims if claim]) == 2
    assert len({claim["id"] for claim in claims if claim}) == 2
    claim = next(claim for claim in claims if claim)
    assert not store.complete(claim["id"], "stale", error=JobError(code="X", message="bad"))
    assert store.get(claim["id"]).state == "running"
    assert all(store.get(identifier).state in ("queued", "running") for identifier in identifiers)


def test_restart_recovery_attempt_limit_and_stale_completion(store):
    now = [100.0]
    store.clock = lambda: now[0]
    identifier = admit(store, [b"a"])
    first = store.claim()
    now[0] += 2
    store.maintain()
    second = store.claim()
    assert second["id"] == identifier and second["token"] != first["token"]
    assert not store.complete(identifier, first["token"], error=JobError(code="X", message="stale"))
    now[0] += 2
    store.maintain()
    assert store.get(identifier).state == "failed"
    assert store.get(identifier).attempt == 2
    assert store.get(identifier).error.code == "WORKER_INTERRUPTED"


def test_cancellation_fences_success_and_scrubs_options(store, sample_pdf_path):
    queued = admit(store, [b"a"], password="test-secret")
    assert store.cancel(queued).state == "cancelled"
    assert store.cancel(queued).state == "cancelled"
    running = admit(store, [sample_pdf_path.read_bytes()])
    claim = store.claim()
    assert store.cancel(running).state == "cancelling"
    result = parse(sample_pdf_path, formats=["text"], ocr="never")
    assert store.complete(running, claim["token"], result=result)
    assert store.get(running).state == "cancelled"
    with store.connection() as db:
        assert all(row[0] is None for row in db.execute("SELECT options FROM jobs"))
    assert not list((store.storage.root / "inputs").iterdir())
    assert not list((store.storage.root / "results").iterdir())


def test_retention_and_orphan_reconciliation(store, sample_pdf_path):
    now = [100.0]
    store.clock = lambda: now[0]
    identifier = admit(store, [sample_pdf_path.read_bytes()])
    claim = store.claim()
    result = parse(sample_pdf_path, formats=["text"], ocr="never")
    assert store.complete(identifier, claim["token"], result=result)
    assert json.loads(store.result(identifier))["data"]["text"] == result.text
    store.storage.path("results", "orphan").write_bytes(b"bad")
    now[0] += store.settings.retention_seconds + 1
    store.maintain()
    assert store.get(identifier).state == "expired"
    with pytest.raises(JobServiceError) as error:
        store.result(identifier)
    assert error.value.status == 410
    assert not list((store.storage.root / "results").iterdir())
    now[0] += store.settings.tombstone_seconds + 1
    store.maintain()
    with pytest.raises(JobServiceError) as error:
        store.get(identifier)
    assert error.value.status == 404


def test_batch_isolation_order_and_pagination(store, sample_pdf_path):
    batch_id = admit(store, [sample_pdf_path.read_bytes(), b"bad PDF"], batch=True)
    batch = store.batch(batch_id, limit=1)
    assert batch.total == 2 and batch.next_cursor == 1
    second = store.batch(batch_id, cursor=1, limit=1)
    assert second.jobs[0].input_index == 1 and second.next_cursor is None
    worker = JobWorker(store)
    try:
        jobs = drive(worker, [batch.jobs[0].job_id, second.jobs[0].job_id])
        assert [job.state for job in jobs] == ["succeeded", "failed"]
        assert store.batch(batch_id).state == "mixed"
    finally:
        worker.close()


def test_worker_deadline_shutdown_and_process_cleanup(store, sample_pdf_path):
    identifier = admit(store, [sample_pdf_path.read_bytes()])
    with store.transaction() as db:
        options = json.loads(db.execute("SELECT options FROM jobs WHERE id=?", (identifier,)).fetchone()[0])
        options["timeout_seconds"] = 0.001
        db.execute("UPDATE jobs SET options=? WHERE id=?", (json.dumps(options), identifier))
    before = {p.pid for p in multiprocessing.active_children()}
    worker = JobWorker(store)
    try:
        assert drive(worker, [identifier])[0].error.code == "PARSE_TIMEOUT"
        other = admit(store, [sample_pdf_path.read_bytes()])
        worker.tick()
        worker.close()
        assert store.get(other).state == "queued"
    finally:
        worker.close()
    assert {p.pid for p in multiprocessing.active_children()} <= before


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUEPARSE_JOBS_ENABLED", "true")
    monkeypatch.setenv("TRUEPARSE_JOBS_ROOT", str(tmp_path / "api-jobs"))
    monkeypatch.setenv("TRUEPARSE_JOBS_MAX_UPLOAD_BYTES", str(1024**2))
    monkeypatch.delenv("TRUEPARSE_API_KEY", raising=False)
    monkeypatch.setattr(JobWorker, "tick", lambda self: None)
    with TestClient(app) as client:
        yield client


def test_api_admission_status_results_and_cancel(api, sample_pdf_path):
    response = api.post(
        "/v1/jobs",
        files={"file": ("report.pdf", sample_pdf_path.read_bytes())},
        data={"options": '{"formats":["text"],"ocr":"never"}'},
        headers={"Idempotency-Key": "one"},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert api.get(job["result_url"]).status_code == 409
    assert api.get(job["status_url"]).json()["state"] == "queued"
    assert api.post(job["status_url"] + "/cancel").json()["state"] == "cancelled"
    assert api.get("/v1/jobs/missing").status_code == 404
    assert api.get("/v1/batches/missing").status_code == 404


def test_api_batch_shared_options_and_bad_intake_cleanup(api, sample_pdf_path):
    response = api.post(
        "/v1/batches",
        files=[("files", ("same.pdf", sample_pdf_path.read_bytes())), ("files", ("same.pdf", b"bad"))],
        data={"options": '{"formats":["text"],"ocr":"never"}'},
    )
    assert response.status_code == 202, response.text
    batch = response.json()
    assert [job["input_index"] for job in batch["jobs"]] == [0, 1]
    assert api.post(batch["status_url"] + "/cancel").json()["state"] == "cancelled"
    assert (
        api.post(
            "/v1/jobs", files={"file": ("bad.pdf", b"x")}, data={"options": '{"images":"path"}'}
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/v1/jobs",
            files={"file": ("bad.pdf", b"x")},
            data={"options": '{"limits":{"max_nodes":99999999}}'},
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/v1/jobs", content=b"truncated", headers={"Content-Type": "multipart/form-data; boundary=foo"}
        ).status_code
        == 422
    )
    assert api.post("/v1/jobs", files={"wrong": ("bad.pdf", b"x")}).status_code == 422
    assert not list((api.app.state.job_worker.store.storage.root / "staging").iterdir())


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/v1/jobs"),
        ("get", "/v1/jobs/a"),
        ("get", "/v1/jobs/a/result"),
        ("post", "/v1/jobs/a/cancel"),
        ("post", "/v1/batches"),
        ("get", "/v1/batches/a"),
        ("post", "/v1/batches/a/cancel"),
    ],
)
def test_all_job_routes_require_auth(api, monkeypatch, method, path):
    monkeypatch.setenv("TRUEPARSE_API_KEY", "secret")
    assert getattr(api, method)(path).status_code == 401


@pytest.mark.parametrize("ordered", [True, False])
def test_local_many_isolated_outcomes_and_cleanup(sample_pdf_path, ordered):
    before = {p.pid for p in multiprocessing.active_children()}
    outcomes = list(
        parse_many(
            [sample_pdf_path, b"invalid"], max_workers=2, ordered=ordered, formats=["text"], ocr="never"
        )
    )
    assert sorted(o.input_index for o in outcomes) == [0, 1]
    by_index = {o.input_index: o for o in outcomes}
    assert by_index[0].result.status == "completed"
    assert by_index[1].error is not None
    assert {p.pid for p in multiprocessing.active_children()} <= before


def test_local_many_is_lazy_and_close_stops_workers(sample_pdf_path):
    consumed = []

    def sources():
        for i in range(100):
            consumed.append(i)
            yield sample_pdf_path

    iterator = parse_many(sources(), max_workers=2, formats=["text"], ocr="never")
    assert not consumed
    before = {p.pid for p in multiprocessing.active_children()}
    with closing(iterator):
        assert next(iterator).result is not None
        assert len(consumed) == 2
    assert {p.pid for p in multiprocessing.active_children()} <= before


@pytest.mark.asyncio
async def test_async_many_close_and_cancellation(sample_pdf_path):
    before = {p.pid for p in multiprocessing.active_children()}
    async with aclosing(
        aparse_many([sample_pdf_path] * 5, max_workers=2, formats=["text"], ocr="never")
    ) as items:
        assert (await anext(items)).result is not None
    items = aparse_many([sample_pdf_path] * 5, max_workers=2, formats=["text"], ocr="never")
    task = asyncio.create_task(anext(items))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await items.aclose()
    assert {p.pid for p in multiprocessing.active_children()} <= before


def test_failed_publication_and_missing_result_are_explicit(store, sample_pdf_path, monkeypatch):
    identifier = admit(store, [sample_pdf_path.read_bytes()])
    claim = store.claim()
    result = parse(sample_pdf_path, formats=["text"], ocr="never")
    import trueparse.jobs.store as module

    original = module.os.replace

    def fail(source, target):
        if "results" in str(target):
            raise OSError("disk full")
        return original(source, target)

    monkeypatch.setattr(module.os, "replace", fail)
    assert store.complete(identifier, claim["token"], result=result)
    assert store.get(identifier).state == "failed"
    assert store.get(identifier).error.code == "ASSET_STORAGE_ERROR"
    assert not list((store.storage.root / "results").iterdir())


def test_idempotency_keys_are_atomic_under_concurrent_admission(store):
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: admit(store, [b"pdf"], key="concurrent"), range(2)))
    assert ids[0] == ids[1]


def test_stale_staging_and_orphans_clean_without_touching_active_upload(store):
    now = [100.0]
    store.clock = lambda: now[0]
    active = store.begin_stage()
    path = store.storage.path("staging", active) / "0"
    path.write_bytes(b"active")
    store.storage.path("inputs", "orphan").write_bytes(b"orphan")
    store.maintain()
    assert path.exists()
    assert not store.storage.path("inputs", "orphan").exists()
    now[0] += store.settings.staging_seconds + 1
    store.maintain()
    assert not path.exists()
    with pytest.raises(JobServiceError):
        store.touch_stage(active)


def test_oversized_and_duplicate_fields_cleanup(api):
    assert api.post("/v1/batches", files=[("files", ("a.pdf", b"x" * (1024**2 + 1)))]).status_code == 413
    assert (
        api.post("/v1/jobs", files=[("file", ("a.pdf", b"a")), ("file", ("b.pdf", b"b"))]).status_code == 422
    )
    assert (
        api.post("/v1/batches", files=[("files", (f"{i}.pdf", b"x")) for i in range(21)]).status_code == 422
    )
    assert (
        api.post("/v1/jobs", files={"file": ("a.pdf", b"a")}, data={"options": "{" * 16385}).status_code
        == 422
    )
    store = api.app.state.job_worker.store
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM stages").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_sdk_job_methods_and_error_codes(store, sample_pdf_path):
    identifier = admit(store, [sample_pdf_path.read_bytes()])
    status = store.get(identifier)
    requests = []

    def transport(request):
        requests.append(request)
        if request.url.path.endswith("/result"):
            return httpx.Response(409, json={"error": {"code": "JOB_NOT_READY", "message": "Not ready"}})
        return httpx.Response(202, json=status.model_dump(mode="json"))

    with TrueParseClient() as client:
        client._http.close()
        client._http = httpx.Client(base_url="http://test", transport=httpx.MockTransport(transport))
        assert client.submit_job(sample_pdf_path, idempotency_key="one").job_id == identifier
        assert requests[-1].headers["Idempotency-Key"] == "one"
        assert client.get_job(identifier).state == "queued"
        assert client.cancel_job(identifier).job_id == identifier
        with pytest.raises(ParseError) as error:
            client.get_job_result(identifier)
        assert error.value.code.value == "JOB_NOT_READY"
        with pytest.raises(TimeoutError):
            client.wait_for_job(identifier, poll_interval=0.001, timeout=0.005)
        with pytest.raises(ValueError):
            client.get_job("../escape")
    assert all(request.url.path.startswith("/v1/jobs") for request in requests)


@pytest.mark.asyncio
async def test_async_remote_helpers_return_typed_records(store):
    identifier = admit(store, [b"a"])
    status = store.cancel(identifier)
    with TrueParseClient() as client:
        client._http.close()
        client._http = httpx.Client(
            base_url="http://test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=status.model_dump(mode="json"))
            ),
        )
        assert (await client.aget_job(identifier)).state == "cancelled"
        assert (await client.await_for_job(identifier, timeout=1)).state == "cancelled"


def test_job_schemas_match_models():
    from trueparse.contracts.schema import build_schema

    for name in ("job", "batch", "outcome"):
        assert get_schema(name) == build_schema(name)


def test_jobs_encrypted_scan_partial_strict_and_blank(store, encrypted_pdf_path, scanned_pdf_path):
    import pymupdf

    store.settings.max_upload_bytes = max(
        store.settings.max_upload_bytes, scanned_pdf_path.stat().st_size + 1
    )

    with pymupdf.open() as pdf:
        pdf.new_page()
        blank = pdf.tobytes()
    identifiers = [
        admit(store, [encrypted_pdf_path.read_bytes()]),
        admit(store, [encrypted_pdf_path.read_bytes()], password="s3cret"),
        admit(store, [scanned_pdf_path.read_bytes()]),
        admit(store, [scanned_pdf_path.read_bytes()], strict=True),
        admit(store, [blank]),
    ]
    worker = JobWorker(store)
    try:
        jobs = drive(worker, identifiers, timeout=40)
        assert jobs[0].error.code == "PDF_PASSWORD_REQUIRED"
        assert jobs[1].state == "succeeded"
        assert json.loads(store.result(identifiers[2]))["status"] == "partial"
        assert jobs[3].error.code == "EXTRACTION_INCOMPLETE"
        assert jobs[4].state == "succeeded"
    finally:
        worker.close()
