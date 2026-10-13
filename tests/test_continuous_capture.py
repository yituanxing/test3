"""Scheduler contract tests. No mock face result is presented as model evidence."""
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def context(tmp_path, monkeypatch):
    from app import main, continuous_capture as jobs
    jobs.shutdown()
    # Previous test workers must be closed before swapping the database.
    with jobs._guard:
        for thread in list(jobs._workers.values()):
            thread.join(timeout=1)
    jobs._shutdown.clear()
    monkeypatch.setattr(main, "DB", tmp_path / "capture.sqlite3")
    main.setup_db()
    with main.db() as conn:
        place_id = conn.execute("SELECT id FROM places LIMIT 1").fetchone()[0]
        src = conn.execute(
            "INSERT INTO sources(place_id,name,kind,uri,enabled) "
            "VALUES(?, 'capture test', 'rtsp', 'rtsp://127.0.0.1:8554/test', 1)",
            (place_id,)
        ).lastrowid
        file_source = conn.execute(
            "INSERT INTO sources(place_id,name,kind,uri,enabled) "
            "VALUES(?, 'file test', 'file', '/unread.mp4', 1)",
            (place_id,)
        ).lastrowid
    yield TestClient(main.app), main, jobs, src, file_source
    jobs.shutdown()
    with jobs._guard:
        for thread in list(jobs._workers.values()):
            thread.join(timeout=1)
    jobs._shutdown.clear()


def test_worker_singleton_and_stop(context, monkeypatch):
    client, main, jobs, src, file_id = context
    lock = threading.Lock()
    calls = []
    working = threading.Event()
    release = threading.Event()

    def process(source_id):
        assert source_id == src
        with lock:
            calls.append(source_id)
        working.set()
        release.wait(2)
        return {"new_samples": 4}

    monkeypatch.setattr(jobs, "_process", process)
    assert client.post(f"/api/capture/jobs/{file_id}/start").status_code == 422
    assert client.post(f"/api/capture/jobs/{src}/start").status_code == 200
    assert working.wait(2)
    assert client.post(f"/api/capture/jobs/{src}/start").status_code == 200
    with lock:
        assert len(calls) == 1  # second start never creates a concurrent worker
    visible = client.get("/api/capture/jobs").json()["jobs"]
    assert len(visible) == 1 and visible[0]["source_id"] == src
    assert "owner_token" not in visible[0]
    stopped = client.post(f"/api/capture/jobs/{src}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["desired_enabled"] is False
    release.set()
    assert eventually(lambda: client.get("/api/capture/jobs").json()["jobs"][0]["state"] == "stopped")
    with lock:
        assert len(calls) == 1
    with main.db() as conn:
        row = conn.execute("SELECT desired_enabled,owner_token FROM capture_jobs WHERE source_id=?",
                           (src,)).fetchone()
        assert row["desired_enabled"] == 0 and row["owner_token"] is None


def test_lease_excludes_other_process_and_reclaims_expired_owner(context, monkeypatch):
    client, main, jobs, src, _ = context
    monkeypatch.setattr(jobs, "_spawn", lambda source_id: None)
    client.post(f"/api/capture/jobs/{src}/start")
    with main.db() as c:
        c.execute("UPDATE capture_jobs SET owner_token='other-instance',"
                  "lease_until='2999-01-01T00:00:00+00:00' WHERE source_id=?", (src,))
    assert jobs._claim(src, "my-instance") is False
    with main.db() as c:
        c.execute("UPDATE capture_jobs SET lease_until='2001-01-01T00:00:00+00:00'"
                  " WHERE source_id=?", (src,))
    assert jobs._claim(src, "my-instance") is True
    with main.db() as c:
        assert c.execute("SELECT owner_token FROM capture_jobs WHERE source_id=?",
                         (src,)).fetchone()[0] == "my-instance"
    jobs._release(src, "my-instance")


def test_failure_code_redacted_and_retry_without_parallel_workers(context, monkeypatch):
    client, main, jobs, src, _ = context
    attempts = []
    def fail(source_id):
        attempts.append(source_id)
        raise HTTPException(502, "Secret rtsp://username:password@localhost/")
    monkeypatch.setattr(jobs, "_process", fail)
    assert client.post(f"/api/capture/jobs/{src}/start").status_code == 200
    def failed_once():
        j = client.get("/api/capture/jobs").json()["jobs"][0]
        return j["state"] == "retrying" and j["failure_count"] >= 1
    assert eventually(failed_once)
    job = client.get("/api/capture/jobs").json()["jobs"][0]
    assert job["last_error_code"] == "HTTP_502"
    assert "password" not in str(job)
    assert client.post(f"/api/capture/jobs/{src}/stop").status_code == 200
    assert eventually(lambda: client.get("/api/capture/jobs").json()["jobs"][0]["state"] == "stopped")


def test_reenable_uses_persisted_desired_state(context, monkeypatch):
    client, main, jobs, src, _ = context
    monkeypatch.setattr(jobs, "_spawn", lambda source_id: None)
    assert client.post(f"/api/capture/jobs/{src}/start").status_code == 200
    with main.db() as c:
        assert c.execute("SELECT desired_enabled FROM capture_jobs WHERE source_id=?",
                         (src,)).fetchone()[0] == 1
    recovered = []
    monkeypatch.setattr(jobs, "_spawn", recovered.append)
    jobs.start_pending()
    assert recovered == [src]
    client.post(f"/api/capture/jobs/{src}/stop")
    recovered.clear()
    jobs.start_pending()
    assert recovered == []
