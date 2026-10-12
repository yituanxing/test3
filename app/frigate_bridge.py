"""Frigate (upstream OSS) HTTP event adapter.

Only creates anonymous person-object detection events; never interprets a
Frigate recognition sub-label as a verified identity.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass(frozen=True)
class ObjectEvent:
    event_id: str
    camera: str
    label: str
    started_at: str
    ended_at: str | None
    score: float | None
    has_snapshot: bool


def parse_person_event(raw: dict) -> ObjectEvent | None:
    """Return a structurally sound person detection or None.

    Track identity is deliberately NOT part of this data interface.
    """
    if not isinstance(raw, dict) or raw.get('label') != 'person' or raw.get('false_positive') is True:
        return None
    event_id = raw.get('id')
    camera = raw.get('camera')
    started = raw.get('start_time')
    if not isinstance(event_id, str) or not event_id or not isinstance(camera, str) or not camera:
        return None
    if not isinstance(started, (int, float)) or isinstance(started, bool) or started < 0:
        return None
    def iso(ts):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec='milliseconds') if ts is not None else None
    end = raw.get('end_time')
    if end is not None and (not isinstance(end, (float, int)) or isinstance(end, bool) or end < started):
        return None
    data = raw.get('data') if isinstance(raw.get('data'), dict) else {}
    score = data.get('top_score', data.get('score'))
    if score is not None and (not isinstance(score, (float, int)) or isinstance(score, bool) or not 0 <= score <= 1):
        score = None
    try:
        start_iso, end_iso = iso(started), iso(end)
    except (ValueError, OSError, OverflowError):
        return None
    return ObjectEvent(event_id, camera, 'person', start_iso, end_iso, score, bool(raw.get('has_snapshot')))
