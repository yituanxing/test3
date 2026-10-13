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
    # Scheduler behavioral fixtures replace its inference callback with a tiny
    # deterministic stub; independent video test below exercises REAL decoder.
    monkeypatch.setattr(jobs, "_make_session", None)
    jobs.shutdown()
    # Previous test workers must be closed before swapping the database.
    with jobs._guard:
        old_threads = list(jobs._workers.values())
    for thread in old_threads:
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
        old_threads = list(jobs._workers.values())
    for thread in old_threads:
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


def test_gb_same_binding_can_enable_only_one_source_and_stops_with_session(context, monkeypatch):
    client, main, jobs, first_source, _ = context
    from app import gb28181
    monkeypatch.setattr(jobs, "_spawn", lambda source_id: None)
    monkeypatch.setattr(gb28181, "_wvp_get", lambda route, params=None: {"code": 0})
    with main.db() as c:
        place = c.execute("SELECT place_id FROM sources WHERE id=?",
                          (first_source,)).fetchone()[0]
        binding = c.execute("""INSERT INTO gb28181_channel_bindings
            (place_id,device_id,channel_id,name,created_at)
            VALUES(?,?,?,?,?)""", (place, "1"*20, "2"*20,
                                   "capture test", main.utcnow())).lastrowid
        second = c.execute("""INSERT INTO sources(place_id,name,kind,uri,enabled)
            VALUES(?,'second live source','rtsp','rtsp://127.0.0.1:8554/second',1)""",
                           (place,)).lastrowid
        session_a = c.execute("""INSERT INTO gb28181_stream_sessions
            (binding_id,source_id,mode,upstream_stream,state,created_at)
            VALUES(?,?,'live','stream_a','active',?)""",
                              (binding,first_source,main.utcnow())).lastrowid
        c.execute("""INSERT INTO gb28181_stream_sessions
            (binding_id,source_id,mode,upstream_stream,state,created_at)
            VALUES(?,?,'live','stream_b','active',?)""",
                  (binding,second,main.utcnow()))
    assert client.post(f"/api/capture/jobs/{first_source}/start").status_code == 200
    assert client.post(f"/api/sources/{first_source}/process?max_frames=1").status_code == 409
    assert client.post(f"/api/sources/{first_source}/process?max_frames=1&worker_mode=true").status_code == 409
    blocked = client.post(f"/api/capture/jobs/{second}/start")
    assert blocked.status_code == 409
    stopped = client.post(f"/api/gb28181/sessions/{session_a}/stop")
    assert stopped.status_code == 200, stopped.text
    with main.db() as c:
        row = c.execute("SELECT desired_enabled FROM capture_jobs WHERE source_id=?",
                        (first_source,)).fetchone()
        assert row["desired_enabled"] == 0
    assert client.post(f"/api/capture/jobs/{second}/start").status_code == 200


def test_live_capture_reuses_actual_decoder_across_sqlite_batches(context, monkeypatch):
    """Real bundled frames, one VideoCapture open, monotonic source frame indices."""
    client, main, jobs, src, _ = context
    from pathlib import Path
    footage = main.ROOT / "samples" / "test-pattern.mp4"
    assert footage.is_file()
    real_open = main.cv2.VideoCapture
    calls = []

    def redirect_rtsp_to_licensed_file(uri):
        calls.append(uri)
        assert uri == "rtsp://127.0.0.1:8554/test"
        return real_open(str(footage))

    monkeypatch.setattr(main.cv2, "VideoCapture", redirect_rtsp_to_licensed_file)
    session = main.LiveCaptureSession(src, batch_frames=20, sample_every=10)
    try:
        first = session.next_batch()
        second = session.next_batch()
        assert first["frames_decoded"] == second["frames_decoded"] == 20
        assert first["new_samples"] == second["new_samples"] == 2
        assert len(calls) == 1, "RTSP must remain open between successful batches"
        with main.db() as c:
            positions = [r[0] for r in c.execute(
                "SELECT frame_index FROM samples WHERE source_id=? ORDER BY id",
                (src,)).fetchall()]
        assert positions == [1, 11, 21, 31]
    finally:
        session.close()

    # A reconnect in the same logical source must not reuse indexes 1,11,...
    recovered = main.LiveCaptureSession(src, batch_frames=20, sample_every=10)
    try:
        third = recovered.next_batch()
        assert third["new_samples"] == 2
        assert len(calls) == 2, "only real reconnect opens a second decoder"
        with main.db() as c:
            positions = [r[0] for r in c.execute(
                "SELECT frame_index FROM samples WHERE source_id=? ORDER BY id",
                (src,)).fetchall()]
        assert positions == [1, 11, 21, 31, 41, 51]
    finally:
        recovered.close()


def test_stop_start_during_old_worker_exit_rearms_capture(context, monkeypatch):
    """Starting while old worker is exiting must not orphan desired_enabled=1."""
    client, main, jobs, src, _ = context
    at_exit = threading.Event()
    allow_exit = threading.Event()
    calls = []
    original_release = jobs._release

    def hold_last_release(source_id, token):
        at_exit.set()
        assert allow_exit.wait(3)
        return original_release(source_id, token)

    def process(source_id):
        calls.append(source_id)
        return {"new_samples": 1}

    monkeypatch.setattr(jobs, "_process", process)
    monkeypatch.setattr(jobs, "_release", hold_last_release)
    try:
        assert client.post(f"/api/capture/jobs/{src}/start").status_code == 200
        assert eventually(lambda: len(calls) >= 1)
        assert client.post(f"/api/capture/jobs/{src}/stop").status_code == 200
        assert at_exit.wait(3), "first worker should enter finalizer"
        # The old worker is *still alive*; previously this start was lost.
        assert client.post(f"/api/capture/jobs/{src}/start").status_code == 200
        allow_exit.set()
        assert eventually(lambda: len(calls) >= 2), "new worker must be rearmed"
    finally:
        allow_exit.set()
        client.post(f"/api/capture/jobs/{src}/stop")
