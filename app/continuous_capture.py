"""Durable, source-scoped continuous capture for local single-service demo.

An SQLite lease prevents two app processes from processing one source at once.
The worker invokes the existing real OpenCV -> detector -> samples pipeline.
This is NOT WVP re-INVITE recovery: a dead upstream stream may need a fresh
GB28181 live session, which remains a separate feature.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from app import access, admin

router = APIRouter(prefix="/api/capture")
_db = None
_process = None
_workers: dict[int, threading.Thread] = {}
_guard = threading.RLock()
_shutdown = threading.Event()
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 15
MAX_BACKOFF_SECONDS = 60


def _now():
    return datetime.now(timezone.utc)


def _stamp(dt=None):
    return (dt or _now()).isoformat(timespec="seconds")


def initialize(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS capture_jobs (
        source_id INTEGER PRIMARY KEY REFERENCES sources(id),
        binding_id INTEGER REFERENCES gb28181_channel_bindings(id),
        desired_enabled INTEGER NOT NULL DEFAULT 0 CHECK(desired_enabled IN (0,1)),
        state TEXT NOT NULL DEFAULT 'stopped',
        owner_token TEXT,
        lease_until TEXT,
        failure_count INTEGER NOT NULL DEFAULT 0,
        successful_batches INTEGER NOT NULL DEFAULT 0,
        sampled_frames INTEGER NOT NULL DEFAULT 0,
        last_success_at TEXT,
        last_error_code TEXT,
        updated_at TEXT NOT NULL
    );
    """)
    # Only one desired worker per real GB device/channel binding, even when
    # WVP creates a NEW RTSP source for another live session.
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_active_gb_capture
        ON capture_jobs(binding_id)
        WHERE desired_enabled=1 AND binding_id IS NOT NULL""")


def register(app, get_db, process):
    global _db, _process
    _db, _process = get_db, process
    app.include_router(router)
    app.add_event_handler("startup", start_pending)
    app.add_event_handler("shutdown", shutdown)


def _row(c, source_id):
    return c.execute("SELECT * FROM capture_jobs WHERE source_id=?", (source_id,)).fetchone()


def _public(row):
    d = dict(row)
    # Never disclose a token that could be used to spoof ownership.
    d.pop("owner_token", None)
    d["desired_enabled"] = bool(d["desired_enabled"])
    return d


def _spawn(source_id):
    with _guard:
        previous = _workers.get(source_id)
        if previous is not None and previous.is_alive():
            return
        t = threading.Thread(target=_run, args=(source_id,), daemon=True,
                             name=f"capture-source-{source_id}")
        _workers[source_id] = t
        t.start()


def start_pending():
    _shutdown.clear()
    with _db() as c:
        sources = [r[0] for r in c.execute(
            "SELECT source_id FROM capture_jobs WHERE desired_enabled=1")]
    for source_id in sources:
        _spawn(source_id)


def shutdown():
    _shutdown.set()
    # Daemon workers finish a bounded batch (or reach the OpenCV timeout).
    # A process restart reclaims an expired lease instead of duplicating work.
    with _guard:
        workers = list(_workers.values())
    for t in workers:
        t.join(timeout=0.25)


def _claim(source_id, token):
    now = _now()
    with _db() as c:
        updated = c.execute("""
          UPDATE capture_jobs
          SET owner_token=?, lease_until=?, state='running', updated_at=?
          WHERE source_id=? AND desired_enabled=1
            AND (owner_token IS NULL OR owner_token=? OR lease_until < ?)
        """, (token, _stamp(now + timedelta(seconds=LEASE_SECONDS)),
              _stamp(now), source_id, token, _stamp(now)))
        return updated.rowcount == 1


def _heartbeat(source_id, token, done):
    while not done.wait(HEARTBEAT_SECONDS):
        with _db() as c:
            changed = c.execute("""
              UPDATE capture_jobs SET lease_until=?, updated_at=?
              WHERE source_id=? AND owner_token=? AND desired_enabled=1
            """, (_stamp(_now() + timedelta(seconds=LEASE_SECONDS)),
                  _stamp(), source_id, token)).rowcount
        if not changed:
            return


def _release(source_id, token):
    with _db() as c:
        c.execute("""
          UPDATE capture_jobs SET owner_token=NULL, lease_until=NULL,
            state=CASE WHEN desired_enabled=1 THEN 'pending' ELSE 'stopped' END,
            updated_at=?
          WHERE source_id=? AND owner_token=?
        """, (_stamp(), source_id, token))


def _run(source_id):
    token = uuid.uuid4().hex
    try:
        while not _shutdown.is_set():
            with _db() as c:
                row = _row(c, source_id)
                if row is None or not row["desired_enabled"]:
                    break
            if not _claim(source_id, token):
                _shutdown.wait(2)
                continue

            done = threading.Event()
            beat = threading.Thread(target=_heartbeat, args=(source_id, token, done),
                                    daemon=True, name=f"capture-heartbeat-{source_id}")
            beat.start()
            result, failure = None, None
            try:
                # This function always runs the actual image decoding/inference path.
                result = _process(source_id)
            except HTTPException as exc:
                failure = f"HTTP_{exc.status_code}"
            except Exception as exc:
                # Do not persist exception messages: OpenCV may include RTSP secrets.
                failure = type(exc).__name__[:80]
            finally:
                done.set()
                beat.join(timeout=1)

            with _db() as c:
                row = _row(c, source_id)
                if row is None or row["owner_token"] != token:
                    break
                if not row["desired_enabled"]:
                    break
                if failure is None:
                    c.execute("""
                      UPDATE capture_jobs SET state='running', failure_count=0,
                        successful_batches=successful_batches+1,
                        sampled_frames=sampled_frames+?,
                        last_success_at=?, last_error_code=NULL, updated_at=?
                      WHERE source_id=? AND owner_token=?
                    """, (int(result.get("new_samples", 0)), _stamp(), _stamp(),
                          source_id, token))
                    delay = 0.5
                else:
                    failures = row["failure_count"] + 1
                    c.execute("""
                      UPDATE capture_jobs SET state='retrying', failure_count=?,
                        last_error_code=?, updated_at=?
                      WHERE source_id=? AND owner_token=?
                    """, (failures, failure, _stamp(), source_id, token))
                    delay = min(MAX_BACKOFF_SECONDS, 2 ** min(failures, 6))
            _shutdown.wait(delay)
    finally:
        _release(source_id, token)
        with _guard:
            if _workers.get(source_id) is threading.current_thread():
                _workers.pop(source_id, None)


@router.get("/jobs")
def jobs(request: Request):
    with _db() as c:
        allowed = {p["id"] for p in admin.place_rows(
            c, access.effective_region(c, request, None))}
        rows = c.execute("""
          SELECT j.*, s.place_id FROM capture_jobs j
          JOIN sources s ON j.source_id=s.id ORDER BY j.source_id
        """).fetchall()
        return {"jobs": [_public(r) for r in rows if r["place_id"] in allowed]}


@router.post("/jobs/{source_id}/start")
def start(source_id: int, request: Request):
    with _db() as c:
        access.check_source(c, request, source_id)
        src = c.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not src["enabled"]:
            raise HTTPException(409, "视频源已停用")
        admin._active_place(c, src["place_id"])
        if src["kind"] not in ("rtsp", "camera"):
            raise HTTPException(422, "持续采集仅支持 RTSP/摄像头，文件请单次分析")
        linked = c.execute("""SELECT binding_id FROM gb28181_stream_sessions
            WHERE source_id=? AND mode='live' AND state='active'""",
            (source_id,)).fetchone()
        if c.execute("""SELECT 1 FROM gb28181_stream_sessions
            WHERE source_id=? AND state='stopped'""", (source_id,)).fetchone():
            raise HTTPException(409, "国标会话已停流，请先重新实时点播")
        try:
            c.execute("""
          INSERT INTO capture_jobs(source_id,binding_id,desired_enabled,state,updated_at)
          VALUES(?,?,1,'pending',?)
          ON CONFLICT(source_id) DO UPDATE SET
            desired_enabled=1, binding_id=excluded.binding_id,
            state=CASE WHEN capture_jobs.owner_token IS NULL
                       THEN 'pending' ELSE capture_jobs.state END,
            updated_at=excluded.updated_at
            """, (source_id, linked['binding_id'] if linked else None, _stamp()))
        except __import__('sqlite3').IntegrityError as exc:
            raise HTTPException(409, "同一国标通道已有持续采集任务；请先停止旧任务") from exc
        row = _row(c, source_id)
    _spawn(source_id)
    return _public(row)


@router.post("/jobs/{source_id}/stop")
def stop(source_id: int, request: Request):
    with _db() as c:
        access.check_source(c, request, source_id)
        row = _row(c, source_id)
        if row is None:
            raise HTTPException(404, "该视频源没有持续采集任务")
        c.execute("""
          UPDATE capture_jobs SET desired_enabled=0,
            state=CASE WHEN owner_token IS NULL THEN 'stopped' ELSE 'stopping' END,
            updated_at=? WHERE source_id=?
        """, (_stamp(), source_id))
        return _public(_row(c, source_id))
