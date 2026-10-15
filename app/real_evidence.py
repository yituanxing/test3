"""Real-input-only operational evidence read model.

Only upstream Frigate person events and decoded-video measurements can enter.
No synthetic P###, fabricated arrivals, identity names, or identity joins.
This is useful *before* a reliable cross-camera person identity exists.
"""
from __future__ import annotations
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pathlib import Path
from app import access, admin, monitoring

router = APIRouter()
_db = None


def register(app, db):
    global _db
    _db = db
    app.include_router(router)


@router.get('/real')
def workbench():
    return FileResponse(Path(__file__).resolve().parents[1] / 'static' / 'real.html')


@router.get('/api/real/evidence')
def get_evidence(request: Request, days: int = Query(7, ge=1, le=31),
                 limit: int = Query(80, ge=1, le=200)):
    """Authoritative region-scoped facts, no business claims derived from P###."""
    with _db() as c:
        region_id = access.effective_region(c, request, None)
        allowed_regions = admin.subtree_ids(c, region_id)
        places = admin.place_rows(c, region_id)
        by_id = {p['id']:p['name'] for p in places}
        video = monitoring.overview(c, days=days, region_id=region_id,
                                    allowed_region_ids=allowed_regions)
        start,end = monitoring.utc_day_window(days)
        events = []
        has_more = False
        if by_id:
            marks = ','.join('?' for _ in by_id)
            rows = c.execute(f"""
              SELECT event_id,camera,place_id,started_at,ended_at,score,
                     has_snapshot,binding_epoch_id
              FROM frigate_object_events
              WHERE label='person' AND place_id IN ({marks})
                    AND started_at>=? AND started_at<?
              ORDER BY started_at DESC,camera,event_id LIMIT ?""",
              (*sorted(by_id), start.isoformat(),end.isoformat(),limit+1)).fetchall()
            has_more = len(rows)>limit
            for row in rows[:limit]:
                r=dict(row)
                events.append({'event_id':r['event_id'],'camera':r['camera'],
                   'place_id':r['place_id'],'place_name':by_id[r['place_id']],
                   'started_at':r['started_at'],'ended_at':r['ended_at'],
                   'confidence':r['score'],'has_snapshot':bool(r['has_snapshot']),
                   'binding_epoch_id':r['binding_epoch_id'],
                   'source':'frigate_actual_upstream_event',
                   'subject_id':None,'identity_confirmed':False})
        # Real photos and archival research do not carry matching person IDs.
        # Only *event* counts are valid, never distinct people/real visits.
        assert all(row['subject_id'] is None for row in events)
        return {
          'mode':'real_evidence_only','window':video['window'],
          'real_event_counts':video['counts'],
          'real_event_timeline':video['timeline'],
          'by_place':video['places'],
          'recent_frigate_events':events,
          'recent_events_truncated':has_more,
          'decoded_media_evidence':video['licensed_research_video'],
          'face_activity_windows':video['face_activity'],
          'proof_sources':{
            'live_person_objects':'Frigate stored person events, keyed by upstream camera + event ID',
            'offline_decoded_frames':'OpenCV video frame captures; independent of person identity',
            'reference_video':'Official CAVIAR local-track annotations (archival frame clock, NOT live)',
          },
          'counts_unique_people':False,'counts_real_site_visits':False,
          'contains_synthetic_identity':False,'contains_synthetic_alerts':False,
          'confirmed_cross_camera_identity':False,
          'missing_required_input':'要分析实际监控，必须实际连接可解码的授权RTSP/国标摄像机，取得真实事件；若没有事件保持0，不生成样例',
          'disclaimer':'这是实际媒体事件和原片证据；匿名检测不等于真人身份、去重人数、到访或风险预警。没有真实事件则显示零，绝不使用P###补齐。'
        }
