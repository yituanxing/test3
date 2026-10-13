"""Conservative, anonymous face-positive ingestion activity windows.

NOT a tracker and NOT deduplication of unique people: one camera/source may
contain multiple people, and the same person can appear in multiple windows.
No face embedding, biometric identity, frame image, or cross-camera linkage
is needed. Windows are an operational count of *batches of positive frames*.
"""
from __future__ import annotations

from datetime import datetime, timezone


DEFAULT_GAP_SECONDS = 20


def _utc(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        # Old SQLite rows are UTC even if stored without an explicit offset.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def count_face_activity_windows(rows, *, gap_seconds: int = DEFAULT_GAP_SECONDS):
    """Group positive sampled-frame *ingest times*, by source and UTC day.

    Rows need place_id, source_id, ingested_at, effective_faces. A gap strictly
    greater than gap_seconds starts a new window; no-face rows do not continue
    or create windows. UTC dates are separate reporting buckets even if an
    episode crosses midnight. Boundaries do not identify distinct persons.

    Returns total, per-place and per-UTC-day counts. Does not mutate input or
    write any durable synthetic person event.
    """
    if not 1 <= gap_seconds <= 3600:
        raise ValueError("face activity gap must be 1..3600 seconds")
    positive = []
    for row in rows:
        if int(row["effective_faces"]) <= 0:
            continue
        source = row["source_id"]
        # Legacy events without source cannot be safely assigned to a stream.
        if source is None:
            continue
        at = _utc(row["ingested_at"])
        positive.append((int(row["place_id"]), int(source), at.date().isoformat(), at))
    positive.sort()

    previous = {}
    places, days = {}, {}
    total = 0
    for place, source, day, at in positive:
        key = (place, source, day)
        last = previous.get(key)
        if last is None or (at - last).total_seconds() > gap_seconds:
            total += 1
            places[place] = places.get(place, 0) + 1
            days[day] = days.get(day, 0) + 1
        previous[key] = at

    return {"total": total, "by_place": places, "by_day": days,
            "gap_seconds": gap_seconds,
            "basis": "face_positive_ingestion_time_source_utc_day",
            "counts_unique_people": False}
