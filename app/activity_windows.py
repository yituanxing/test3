"""Anonymous face-positive sampling activity windows, NOT people or visits.

Windows group frame records from the SAME source and UTC calendar day when
consecutive positive ingestion timestamps are no more than 20 seconds apart.
No face embeddings, person identities, trajectories or cross-camera matches.
One window may include several people; one person may make several windows.
"""
from __future__ import annotations

from datetime import datetime, timezone

DEFAULT_GAP_SECONDS = 20


def _utc(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_face_activity_windows(rows, *, gap_seconds: int = DEFAULT_GAP_SECONDS):
    """Create read-only operational windows from DB samples/dicts.

    Each row must have place_id, source_id, ingested_at, effective_faces.
    Zero-face and source-less records are excluded and do not generate a
    window. Counts describe observations, never unique biological persons.
    """
    if not 1 <= gap_seconds <= 3600:
        raise ValueError("face activity gap must be 1..3600 seconds")
    positives = []
    for row in rows:
        faces = int(row["effective_faces"])
        if faces <= 0 or row["source_id"] is None:
            continue
        at = _utc(row["ingested_at"])
        positives.append((int(row["place_id"]), int(row["source_id"]),
                          at.date().isoformat(), at, faces))
    positives.sort()
    windows = []
    last_by_key = {}
    for place, source, day, at, faces in positives:
        key = (place, source, day)
        current = last_by_key.get(key)
        if current is None or (
                at - _utc(current["last_positive_utc"])).total_seconds() > gap_seconds:
            current = {
                "place_id": place, "source_id": source, "date_utc": day,
                "started_utc": at.isoformat(),
                "last_positive_utc": at.isoformat(),
                "positive_sampled_frames": 0,
                "effective_face_detections": 0,
            }
            windows.append(current)
            last_by_key[key] = current
        current["last_positive_utc"] = at.isoformat()
        current["positive_sampled_frames"] += 1
        current["effective_face_detections"] += faces
    return windows


def count_face_activity_windows(rows, *, gap_seconds: int = DEFAULT_GAP_SECONDS):
    """Aggregate operational windows without assigning identities."""
    windows = build_face_activity_windows(rows, gap_seconds=gap_seconds)
    places, days = {}, {}
    for window in windows:
        place = window["place_id"]
        day = window["date_utc"]
        places[place] = places.get(place, 0) + 1
        days[day] = days.get(day, 0) + 1
    return {
        "total": len(windows), "by_place": places, "by_day": days,
        "gap_seconds": gap_seconds,
        "basis": "face_positive_ingestion_time_source_utc_day",
        "counts_unique_people": False,
    }
