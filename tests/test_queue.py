import time

from fastapi.testclient import TestClient

from trueparse.api.routes import app
from trueparse.core.config import ParseOptions
from trueparse.workers.job_store import SQLiteJobStore
from trueparse.workers.manager import JobManager

client = TestClient(app)


def test_sqlite_job_store(tmp_path):
    db_file = tmp_path / "test_jobs.db"
    store = SQLiteJobStore(db_path=db_file)

    job = store.create_job(
        job_id="job_test_001",
        source_file="test.pdf",
        file_path=str(tmp_path / "test.pdf"),
        batch_id="batch_001",
        total_pages=5,
    )
    assert job.status == "queued"
    assert job.job_id == "job_test_001"

    store.update_progress("job_test_001", current_page=2, total_pages=5, stage="extracting")
    j_updated = store.get_job("job_test_001")
    assert j_updated.status == "processing"
    assert j_updated.current_page == 2
    assert j_updated.percent == 40.0

    store.complete_job("job_test_001", result={"document_id": "doc_123"})
    j_done = store.get_job("job_test_001")
    assert j_done.status == "completed"
    assert j_done.percent == 100.0
    assert j_done.result["document_id"] == "doc_123"

    batch_jobs = store.get_batch_jobs("batch_001")
    assert len(batch_jobs) == 1


def test_job_manager_execution(tmp_path, sample_pdf_path):
    db_file = tmp_path / "manager_jobs.db"
    manager = JobManager(max_workers=2, db_path=str(db_file))

    out_dir = tmp_path / "output"
    options = ParseOptions(output_path=str(out_dir), max_pages=2)
    job = manager.submit_job(file_path=sample_pdf_path, options=options)

    # Wait for completion
    for _ in range(40):
        status = manager.get_job_status(job.job_id)
        if status.status in ("completed", "failed"):
            break
        time.sleep(0.2)

    final_status = manager.get_job_status(job.job_id)
    assert final_status.status == "completed"
    assert final_status.result is not None
    assert final_status.result["page_count"] == 2


def test_api_parse_async_and_status(tmp_path, sample_pdf_path):
    assert sample_pdf_path.exists()
    with open(sample_pdf_path, "rb") as f:
        resp = client.post(
            "/v1/documents/parse-async",
            files={"file": (sample_pdf_path.name, f, "application/pdf")},
            data={"max_pages": 1},
        )
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] in ("queued", "processing")
    job_id = data["job_id"]

    # Poll status
    for _ in range(40):
        status_resp = client.get(f"/v1/documents/jobs/{job_id}")
        assert status_resp.status_code == 200
        job_data = status_resp.json()
        if job_data["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)

    status_resp = client.get(f"/v1/documents/jobs/{job_id}")
    assert status_resp.status_code == 200
    final_job = status_resp.json()
    assert final_job["status"] == "completed"
    assert final_job["result"]["page_count"] == 1


def test_api_batch_async(tmp_path, sample_pdf_path):
    assert sample_pdf_path.exists()
    # Create a distinct second test PDF
    pdf2 = tmp_path / "distinct.pdf"
    import pymupdf
    d2 = pymupdf.open()
    page = d2.new_page(width=595, height=842)
    page.insert_text((72, 72), "Distinct batch document sample text content.")
    d2.save(str(pdf2))
    d2.close()

    with open(sample_pdf_path, "rb") as f1, open(pdf2, "rb") as f2:
        resp = client.post(
            "/v1/batches/parse-async",
            files=[
                ("files", ("doc1.pdf", f1, "application/pdf")),
                ("files", ("doc2.pdf", f2, "application/pdf")),
            ],
            data={"max_pages": 1},
        )
    assert resp.status_code == 202
    data = resp.json()
    assert "batch_id" in data
    assert data["total_documents"] == 2
    batch_id = data["batch_id"]

    # Poll batch status
    for _ in range(60):
        batch_resp = client.get(f"/v1/batches/{batch_id}")
        assert batch_resp.status_code == 200
        batch_data = batch_resp.json()
        if batch_data["status"] in ("completed", "failed", "partial_failure"):
            break
        time.sleep(0.3)

    batch_resp = client.get(f"/v1/batches/{batch_id}")
    assert batch_resp.status_code == 200
    final_batch = batch_resp.json()
    assert final_batch["status"] == "completed"
    assert final_batch["completed_count"] == 2


def test_stale_jobs_are_failed_on_restart(tmp_path):
    """Jobs left mid-flight by a dead process must not hang at 'processing'."""
    db_file = tmp_path / "stale_jobs.db"
    store = SQLiteJobStore(db_path=db_file)
    store.create_job(job_id="job_stuck", source_file="a.pdf", file_path="a.pdf")
    store.update_progress("job_stuck", current_page=1, total_pages=10, stage="page_1")

    recovered = store.fail_stale_jobs("worker restarted")
    assert recovered == 1

    job = store.get_job("job_stuck")
    assert job.status == "failed"
    assert "restarted" in job.error


def test_completed_jobs_survive_stale_sweep(tmp_path):
    db_file = tmp_path / "done_jobs.db"
    store = SQLiteJobStore(db_path=db_file)
    store.create_job(job_id="job_done", source_file="a.pdf", file_path="a.pdf")
    store.complete_job("job_done", result={"document_id": "doc_1"})

    assert store.fail_stale_jobs("worker restarted") == 0
    assert store.get_job("job_done").status == "completed"


def test_process_pool_executes_a_real_job(tmp_path, sample_pdf_path):
    """Parsing is CPU-bound, so the default worker mode is processes."""
    db_file = tmp_path / "process_jobs.db"
    manager = JobManager(max_workers=2, db_path=str(db_file), worker_mode="process")
    try:
        options = ParseOptions(output_path=str(tmp_path / "proc_out"), max_pages=1)
        job = manager.submit_job(file_path=sample_pdf_path, options=options)

        for _ in range(150):
            status = manager.get_job_status(job.job_id)
            if status.status in ("completed", "failed"):
                break
            time.sleep(0.2)

        final = manager.get_job_status(job.job_id)
        assert final.status == "completed", final.error
        assert final.result["page_count"] == 1
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_worker_mode_falls_back_to_threads_when_requested(tmp_path):
    manager = JobManager(max_workers=2, db_path=str(tmp_path / "t.db"), worker_mode="thread")
    try:
        assert manager.worker_mode == "thread"
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)
