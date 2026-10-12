"""Deterministic tests for the *real* bridge without inventing live-CI results."""
import pytest
from fastapi.testclient import TestClient
from app import bridge


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "DB_PATH", tmp_path / "events.sqlite3")
    return TestClient(bridge.app)


def test_anonymous_event_persistence_and_idempotency(client, monkeypatch):
    events = [
        {"id": "fr-001", "camera": "demo", "label": "person",
         "start_time": 100.0, "end_time": 104.0, "false_positive": False},
        {"id": "fr-002", "camera": "demo", "label": "person",
         "start_time": 110.0, "end_time": 115.0},
        {"id": "fr-003", "camera": "demo", "label": "cat",
         "start_time": 112.0, "end_time": 116.0},
    ]
    monkeypatch.setattr(bridge, "fetch_frigate_json", lambda path: events)
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.post("/api/frigate/cameras", json={"camera": "demo", "place_id": 1}).status_code == 200
    a = client.post("/api/frigate/sync").json()
    b = client.post("/api/frigate/sync").json()
    assert a["inserted"] == 2
    assert b["inserted"] == 0
    rows = client.get("/api/frigate/events").json()
    assert {row["event_id"] for row in rows} == {"fr-001", "fr-002"}
    assert all(row["source"] == "frigate" for row in rows)
    counts = client.get("/api/statistics").json()
    assert counts["counts_are_unique_people"] is False
    assert counts["by_camera"][0]["detected_object_events"] == 2


def test_camera_binding_reassignment_requires_review(client):
    assert client.post("/api/frigate/cameras", json={"camera": "demo", "place_id": 1}).status_code == 200
    assert client.post("/api/frigate/cameras", json={"camera": "demo", "place_id": 2}).status_code == 409
    assert client.get("/api/frigate/cameras").json() == [{"camera": "demo", "place_id": 1}]


def test_no_events_is_not_fabricated(client, monkeypatch):
    monkeypatch.setattr(bridge, "fetch_frigate_json", lambda path: [])
    client.post("/api/frigate/cameras", json={"camera": "demo", "place_id": 1})
    assert client.post("/api/frigate/sync").json()["inserted"] == 0
    assert client.get("/api/frigate/events").json() == []
