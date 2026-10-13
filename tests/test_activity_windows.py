"""Anonymous face-positive activity windows, not distinct-person recognition."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from app.activity_windows import count_face_activity_windows


def row(place, source, timestamp, boxes=1):
    return {"place_id":place, "source_id":source,
            "ingested_at":timestamp, "effective_faces":boxes}


def test_window_boundaries_source_isolation_and_negative_frames():
    rows = [
        row(1, 1, "2026-10-12T12:00:00Z"),
        row(1, 1, "2026-10-12T12:00:01+00:00", 2),
        row(1, 1, "2026-10-12T12:00:21Z"),  # exactly 20s since prior: same
        row(1, 1, "2026-10-12T12:00:42Z"),  # 21s apart: new window
        row(1, 1, "2026-10-12T12:01:30Z", 0),  # no positive detection
        row(1, 2, "2026-10-12T12:00:21Z"),  # second camera != same window
        row(2, 1, "2026-10-12T12:00:21Z"),  # different place != same window
        row(1, 1, "2026-10-13T00:00:01Z"),  # new UTC day
        row(1, None, "2026-10-12T12:00:00Z"),  # legacy null source excluded
    ]
    result = count_face_activity_windows(list(reversed(rows)))
    assert result["total"] == 5
    assert result["by_place"] == {1: 4, 2: 1}
    assert result["by_day"] == {"2026-10-12":4, "2026-10-13":1}
    assert result["counts_unique_people"] is False
    assert result == count_face_activity_windows(rows)
    with pytest.raises(ValueError):
        count_face_activity_windows(rows, gap_seconds=0)


def test_no_face_detection_means_zero_activity():
    assert count_face_activity_windows([
        row(1, 1, "2026-10-12T12:00:00Z", 0),
        row(1, 1, "2026-10-12T12:01:00Z", 0),
    ])["total"] == 0


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "DB", tmp_path / "activity.sqlite3")
    main.setup_db()
    return TestClient(main.app), main


def test_monitoring_face_positive_windows_are_separate_from_people_and_frames(app_client):
    client, main = app_client
    with main.db() as conn:
        places = [r["id"] for r in conn.execute(
            "SELECT id FROM places WHERE active=1 ORDER BY id LIMIT 2")]
        assert len(places) == 2
        ids = []
        for place in places:
            ids.append(conn.execute(
                "INSERT INTO sources(place_id,name,kind,uri) VALUES(?,?,?,?)",
                (place, "activity unit", "file", "/unused/fixture.mp4")
            ).lastrowid)
        now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        points = [
            (ids[0], places[0], now - timedelta(seconds=90), 1),
            (ids[0], places[0], now - timedelta(seconds=4), 1),
            (ids[0], places[0], now - timedelta(seconds=3), 2),
            (ids[0], places[0], now - timedelta(seconds=2), 1),
            (ids[0], places[0], now - timedelta(seconds=1), 0),
            (ids[1], places[1], now - timedelta(seconds=1), 1),
        ]
        conn.executemany("""INSERT INTO samples
          (source_id,place_id,origin,ingested_at,frame_index,detected_faces,detector)
          VALUES(?,?,'video',?,?,?,'haar')""",
          [(src,place,dt.isoformat(),idx,boxes)
           for idx,(src,place,dt,boxes) in enumerate(points,1)])
    response = client.get("/api/monitoring/overview?days=7")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["counts"]["sampled_frames"] == 6
    assert data["counts"]["face_detections"] == 6
    assert data["face_activity"]["counts_unique_people"] is False
    assert data["face_activity"]["gap_seconds"] == 20
    assert data["face_activity"]["windows"] == 3
    assert sum(p["face_activity_windows"] for p in data["places"]) == 3
    assert sum(d["face_activity_windows"] for d in data["timeline"]) == 3
    assert data["counts"]["person_objects"] == 0
    assert "face_activity_windows" in data["provenance"]
