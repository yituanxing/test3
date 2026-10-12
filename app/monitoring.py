"""Read-only, provenance-preserving monitoring aggregations.

Counts here are *object events* or *sampled frames*, NOT people or visits.
The synthetic business simulator is never mixed into these results.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def _to_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def overview(conn, *, days: int = 7, now: datetime | None = None,
             region_id: int | None = None, allowed_region_ids: set[int] | None = None) -> dict:
    """Aggregate raw upstream measurements without guessing person identities.

    Uses a fixed UTC window [today-days+1, tomorrow). Event counts are keyed
    by Frigate event id. Frame detections are kept in separate measurements.
    """
    if not 1 <= days <= 31:
        raise ValueError('days must be in 1..31')
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=days)
    places = [dict(r) for r in conn.execute('SELECT id,name,region_id FROM places ORDER BY id')]
    if allowed_region_ids is not None:
        places = [p for p in places if p['region_id'] in allowed_region_ids]
    labels = [(start+timedelta(days=i)).date().isoformat() for i in range(days)]
    timeline = {day:{'person_objects':0,'sampled_frames':0,'face_detections':0} for day in labels}
    by_place = {p['id']:{'place_id':p['id'],'place_name':p['name'],
                         'person_objects':0,'active_objects':0,'sampled_frames':0,
                         'face_detections':0,'cameras':[]} for p in places}
    bindings = conn.execute('''SELECT b.camera,b.place_id FROM frigate_camera_bindings b
                               ORDER BY b.camera''')
    for r in bindings:
        if r['place_id'] in by_place:
            by_place[r['place_id']]['cameras'].append(r['camera'])
    event_rows = conn.execute('''SELECT place_id,started_at,ended_at,event_id FROM frigate_object_events
                                 WHERE started_at >= ? AND started_at < ?''',
                              (start.isoformat(),end.isoformat()))
    for r in event_rows:
        dt = _to_utc(r['started_at'])
        label=dt.date().isoformat()
        if label not in timeline or r['place_id'] not in by_place:
            continue
        timeline[label]['person_objects'] += 1
        by_place[r['place_id']]['person_objects'] += 1
        if r['ended_at'] is None:
            by_place[r['place_id']]['active_objects'] += 1
    sample_rows = conn.execute('''SELECT place_id,ingested_at,detected_faces,
             COALESCE((SELECT corrected_faces FROM reviews r WHERE r.sample_id=s.id
             ORDER BY r.id DESC LIMIT 1),detected_faces) AS effective_faces
             FROM samples s WHERE s.origin='video' AND ingested_at >= ? AND ingested_at < ?''',
             (start.isoformat(),end.isoformat()))
    for r in sample_rows:
        dt = _to_utc(r['ingested_at']); label=dt.date().isoformat()
        if label not in timeline or r['place_id'] not in by_place:
            continue
        timeline[label]['sampled_frames'] += 1
        timeline[label]['face_detections'] += r['effective_faces']
        by_place[r['place_id']]['sampled_frames'] += 1
        by_place[r['place_id']]['face_detections'] += r['effective_faces']
    counts = {'person_objects':sum(r['person_objects'] for r in by_place.values()),
              'sampled_frames':sum(r['sampled_frames'] for r in by_place.values()),
              'face_detections':sum(r['face_detections'] for r in by_place.values())}
    return {
        'window':{'start_utc':start.isoformat(),'end_exclusive_utc':end.isoformat(),'days':days},
        'counts':counts,
        'timeline':[{'day':k,**v} for k,v in timeline.items()],
        'places':list(by_place.values()),
        'scope':{'region_id':region_id,'includes_descendants':True},
        'provenance':{
            'person_objects':'Frigate person事件ID（可能包含同一人多次，不是身份去重人数）',
            'sampled_frames':'OpenCV真实视频抽取后保存的帧观测（不含演示数据）',
            'face_detections':'抽帧的人脸检测框数量之和（可经人工复核，不是人数）',
            'synthetic_business_isolated':True,
        },
    }
