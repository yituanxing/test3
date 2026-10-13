"""Read-only, provenance-preserving monitoring aggregations.

Counts here are *object events* or *sampled frames*, NOT people or visits.
The synthetic business simulator is never mixed into these results.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from app.activity_windows import (count_face_activity_windows,
    build_face_activity_windows, DEFAULT_GAP_SECONDS)


def _to_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def utc_day_window(days: int, *, now: datetime | None = None):
    """One UTC calendar-day window contract shared by all real-video counters.

    Half-open [start,end); historical research clips have no real UTC shooting
    date and must NEVER enter this calendar-day measurement.
    """
    if not 1 <= days <= 31:
        raise ValueError('days must be in 1..31')
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end=now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
    return end-timedelta(days=days),end


def overview(conn, *, days: int = 7, now: datetime | None = None,
             region_id: int | None = None, allowed_region_ids: set[int] | None = None) -> dict:
    """Aggregate raw upstream measurements without guessing person identities.

    Uses a fixed UTC window [today-days+1, tomorrow). Event counts are keyed
    by Frigate event id. Frame detections are kept in separate measurements.
    """
    start,end=utc_day_window(days,now=now)
    places = [dict(r) for r in conn.execute('SELECT id,name,region_id FROM places ORDER BY id')]
    if allowed_region_ids is not None:
        places = [p for p in places if p['region_id'] in allowed_region_ids]
    labels = [(start+timedelta(days=i)).date().isoformat() for i in range(days)]
    timeline = {day:{'person_objects':0,'sampled_frames':0,'face_detections':0,
                      'face_activity_windows':0} for day in labels}
    by_place = {p['id']:{'place_id':p['id'],'place_name':p['name'],
                         'person_objects':0,'active_objects':0,'sampled_frames':0,
                         'face_detections':0,'face_activity_windows':0,
                         'cameras':[]} for p in places}
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
    sample_rows = conn.execute('''SELECT place_id,source_id,ingested_at,detected_faces,
             COALESCE((SELECT corrected_faces FROM reviews r WHERE r.sample_id=s.id
             ORDER BY r.id DESC LIMIT 1),detected_faces) AS effective_faces
             FROM samples s WHERE s.origin='video' AND ingested_at >= ? AND ingested_at < ?''',
             (start.isoformat(),end.isoformat())).fetchall()
    for r in sample_rows:
        dt = _to_utc(r['ingested_at']); label=dt.date().isoformat()
        if label not in timeline or r['place_id'] not in by_place:
            continue
        timeline[label]['sampled_frames'] += 1
        timeline[label]['face_detections'] += r['effective_faces']
        by_place[r['place_id']]['sampled_frames'] += 1
        by_place[r['place_id']]['face_detections'] += r['effective_faces']
    # A positive sequence on a single source, separated by <=20s of ingest
    # time, is one anonymous *activity window*. This is NOT a person visit
    # and does not imply one tracked or uniquely counted human.
    activity = count_face_activity_windows(sample_rows, gap_seconds=DEFAULT_GAP_SECONDS)
    for place_id, value in activity['by_place'].items():
        if place_id in by_place:
            by_place[place_id]['face_activity_windows'] = value
    for day, value in activity['by_day'].items():
        if day in timeline:
            timeline[day]['face_activity_windows'] = value
    visible_windows = sum(p['face_activity_windows'] for p in by_place.values())
    counts = {'person_objects':sum(r['person_objects'] for r in by_place.values()),
              'sampled_frames':sum(r['sampled_frames'] for r in by_place.values()),
              'face_detections':sum(r['face_detections'] for r in by_place.values())}
    # Optional licensed reference footage is NEVER counted as a real-time
    # monitoring observation: its annotation clock is archival video-relative,
    # not today, and two camera-local tracks can correspond to one person.
    official = conn.execute("""SELECT i.source_id,s.place_id,
                (SELECT COUNT(*) FROM public_cctv_track_boxes b
                 WHERE b.source_id=i.source_id) AS boxes,
                (SELECT COUNT(DISTINCT b.camera_local_track_id)
                 FROM public_cctv_track_boxes b WHERE b.source_id=i.source_id) AS tracks
            FROM public_cctv_imports i JOIN sources s ON s.id=i.source_id""")
    dataset_rows=[dict(row) for row in official if row['place_id'] in by_place]
    dataset_evidence={
        'fixed_camera_sources':len(dataset_rows),
        'camera_local_tracks':sum(row['tracks'] for row in dataset_rows),
        'manual_person_boxes':sum(row['boxes'] for row in dataset_rows),
        'basis':'CC BY-SA CAVIAR official ground truth, source/camera local; archival clip-relative evidence, not live people',
        'counts_unique_people':False,'counts_for_date_window':False,
        'distinct_from_current_synthetic_business':True,
    }
    return {
        'window':{'start_utc':start.isoformat(),'end_exclusive_utc':end.isoformat(),'days':days},
        'counts':counts,
        'licensed_research_video':dataset_evidence,
        'face_activity':{
            'windows':visible_windows,
            'gap_seconds':activity['gap_seconds'],
            'basis':activity['basis'],
            'counts_unique_people':False,
            'note':'同一视频源按UTC日及采集入库时间归并连续有脸抽样帧；并非人数、到访次数或身份去重。不同人可能落入同一窗口，同一人也可能跨窗口。',
        },
        'timeline':[{'day':k,**v} for k,v in timeline.items()],
        'places':list(by_place.values()),
        'scope':{'region_id':region_id,'includes_descendants':True},
        'provenance':{
            'person_objects':'Frigate person事件ID（可能包含同一人多次，不是身份去重人数）',
            'sampled_frames':'OpenCV真实视频抽取后保存的帧观测（不含演示数据）',
            'face_detections':'抽帧的人脸检测框数量之和（可经人工复核，不是人数）',
            'face_activity_windows':'按视频源、UTC日和入库时间间隔归并的连续有脸采集窗口；非独立人数，不能当作准确去重',
            'synthetic_business_isolated':True,
        },
    }


def face_activity_details(conn, *, days: int = 7, limit: int = 50,
                          region_id: int | None = None,
                          allowed_region_ids: set[int] | None = None) -> dict:
    """Authorized read-only source-level detail behind overview.window count.

    Never emits stream URLs, face crops or inferred person IDs. Fetches the
    full requested time window before limiting the display, so counts agree
    with monitoring.overview instead of hiding older windows behind LIMIT.
    """
    if not 1 <= limit <= 250:
        raise ValueError("invalid display limit")
    start,end=utc_day_window(days)
    places = [dict(row) for row in conn.execute(
        "SELECT id,name,region_id FROM places ORDER BY id")]
    if allowed_region_ids is not None:
        places = [p for p in places if p["region_id"] in allowed_region_ids]
    place_names = {p["id"]:p["name"] for p in places}
    result = {
        "window":{"start_utc":start.isoformat(),
                  "end_exclusive_utc":end.isoformat(),"days":days},
        "total_windows":0, "windows":[], "bounded_by_limit":limit,
        "counts_unique_people":False, "gap_seconds":DEFAULT_GAP_SECONDS,
        "note":"同源连续有脸抽样帧的匿名采集窗口，不代表到访或唯一人数。数据来自视频入库时间，不是摄像机物理拍摄时间。",
    }
    if not place_names:
        return result
    placeholders = ",".join("?" for _ in place_names)
    samples = conn.execute(f"""SELECT s.place_id,s.source_id,s.ingested_at,
         COALESCE((SELECT r.corrected_faces FROM reviews r
                   WHERE r.sample_id=s.id ORDER BY r.id DESC LIMIT 1),
                  s.detected_faces) AS effective_faces
         FROM samples s
         WHERE s.origin='video' AND s.source_id IS NOT NULL
           AND s.ingested_at>=? AND s.ingested_at<?
           AND s.place_id IN ({placeholders})""",
         (start.isoformat(),end.isoformat(),*sorted(place_names))).fetchall()
    windows = build_face_activity_windows(samples)
    source_names = {r["id"]:r["name"] for r in conn.execute(
        f"SELECT id,name FROM sources WHERE place_id IN ({placeholders})",
        tuple(sorted(place_names)))}
    windows.sort(key=lambda w:(w["last_positive_utc"],w["started_utc"],
                               w["source_id"]),reverse=True)
    result["total_windows"] = len(windows)
    result["windows"] = [{
        **w, "place_name":place_names[w["place_id"]],
        "source_name":source_names.get(w["source_id"],"（视频源已删除）"),
    } for w in windows[:limit]]
    return result
