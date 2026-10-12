"""Minimal independently testable Frigate -> FastAPI -> SQLite integration.

This service stores anonymous detected-object events, not biometric templates or identities.
Its event count is NOT a count of unique people.
"""
from __future__ import annotations

import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import json
from contextlib import contextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI(title="ZD anonymous real-video integration")
DB_PATH = Path(os.environ.get("ZD_DB_PATH", ".ci-data/bridge.sqlite3"))
FRIGATE_URL = os.environ.get("FRIGATE_URL", "http://127.0.0.1:5000").rstrip("/")


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        con.executescript("""
            PRAGMA busy_timeout=15000;
            CREATE TABLE IF NOT EXISTS bindings (
                camera TEXT PRIMARY KEY, place_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS detections (
                event_id TEXT PRIMARY KEY, camera TEXT NOT NULL,
                place_id INTEGER NOT NULL, label TEXT NOT NULL,
                start_time REAL, end_time REAL, source TEXT NOT NULL
            );
        """)
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


class CameraBinding(BaseModel):
    camera: str = Field(min_length=1, max_length=100)
    place_id: int = Field(gt=0)


@app.get("/api/health")
def health():
    with db() as con:
        con.execute("SELECT 1")
    return {"status": "ok", "mode": "anonymous-object-events"}


@app.post("/api/frigate/cameras")
def bind_camera(binding: CameraBinding):
    with db() as con:
        existing = con.execute("SELECT place_id FROM bindings WHERE camera=?", (binding.camera,)).fetchone()
        if existing and existing["place_id"] != binding.place_id:
            raise HTTPException(409, "Changing a binding requires explicit migration/review")
        con.execute(
            "INSERT OR IGNORE INTO bindings(camera, place_id) VALUES (?,?)",
            (binding.camera, binding.place_id))
    return binding.model_dump()


@app.get("/api/frigate/cameras")
def cameras():
    with db() as con:
        return [dict(row) for row in con.execute("SELECT * FROM bindings ORDER BY camera")]


def fetch_frigate_json(path: str):
    try:
        with urllib.request.urlopen(FRIGATE_URL + path, timeout=15) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, ValueError) as exc:
        raise HTTPException(502, "Frigate unavailable or response not JSON") from exc


@app.get("/api/frigate/status")
def status():
    stats = fetch_frigate_json("/api/stats")
    cameras = stats.get("cameras", {})
    return {
        "cameras": {
            cam: {"camera_fps": info.get("camera_fps", 0),
                  "detection_fps": info.get("detection_fps", 0)}
            for cam, info in cameras.items()
        }
    }


@app.post("/api/frigate/sync")
def sync(limit: int = Query(default=200, ge=1, le=500)):
    upstream = fetch_frigate_json("/api/events?" + urllib.parse.urlencode({"limit": limit}))
    if not isinstance(upstream, list):
        raise HTTPException(502, "Expected Frigate events array")
    inserted = 0
    skipped = 0
    with db() as con:
        bindings = {row["camera"]: row["place_id"] for row in
                    con.execute("SELECT camera, place_id FROM bindings")}
        for event in upstream:
            if (not isinstance(event, dict) or event.get("label") != "person"
                    or event.get("false_positive") is True):
                skipped += 1
                continue
            camera = event.get("camera")
            event_id = event.get("id")
            if camera not in bindings or not isinstance(event_id, str) or not event_id:
                skipped += 1
                continue
            cur = con.execute(
                """INSERT OR IGNORE INTO detections
                    (event_id, camera, place_id, label, start_time, end_time, source)
                    VALUES (?, ?, ?, 'person', ?, ?, 'frigate')""",
                (event_id, camera, bindings[camera], event.get("start_time"), event.get("end_time")))
            inserted += cur.rowcount
    return {"upstream_count": len(upstream), "inserted": inserted, "skipped": skipped}


@app.get("/api/frigate/events")
def events(limit: int = Query(default=500, ge=1, le=1000)):
    with db() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM detections ORDER BY start_time DESC, event_id DESC LIMIT ?", (limit,))]


@app.get("/api/statistics")
def statistics():
    with db() as con:
        rows = [dict(r) for r in con.execute(
            """SELECT place_id, camera, COUNT(*) AS detected_object_events
               FROM detections GROUP BY place_id, camera ORDER BY place_id, camera""")]
    return {"counts_are_unique_people": False, "by_camera": rows}
