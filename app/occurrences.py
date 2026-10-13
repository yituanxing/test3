"""Provider-scoped anonymous occurrence contract for fixed-camera Excel analytics.

100 CAVIAR annotated frames for one local track = one *camera occurrence*,
not 100 people. The same local ID in another camera stays independent.
Frigate timestamps are UTC events; CAVIAR offsets are archival frames.
No global identity, face recognition, visit inference, or P### roster mixing.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from fastapi import APIRouter, HTTPException, Query, Request
from app import access, admin, monitoring

router=APIRouter()
_db=None
MAX_PAGE=200


def register(app,db):
    global _db
    _db=db
    app.include_router(router)


def _allowed(c,request):
    return {r['id']:r for r in admin.place_rows(
        c,access.effective_region(c,request,None))}


def _key(provider,source_key,track_key):
    return sha256((provider+'\x00'+source_key+'\x00'+track_key).encode('utf-8')).hexdigest()[:24]


def _scoped_rows(c,allowed,*,limit=500,offset=0):
    """Single deterministic SQL query. Scope restriction happens BEFORE LIMIT.

    Avoid the old global LIMIT 1500 that silently discarded a county's
    legitimate events when other jurisdictions had more recent recordings.
    """
    if not allowed:
        return []
    keys=sorted(allowed)
    marks=','.join('?' for _ in keys)
    query=f"""WITH scoped AS (
        SELECT 'caviar_ground_truth' AS provider,
          i.source_id AS source_id, i.camera_view AS camera_key,
          i.video_sha256 AS file_sha, s.place_id AS venue_id,
          b.camera_local_track_id AS track_id,
          MIN(b.frame_number) AS first_seen,MAX(b.frame_number) AS last_seen,
          COUNT(*) AS annotation_frames,NULL AS score,
          NULL AS binding_epoch_id
        FROM public_cctv_track_boxes b
        JOIN public_cctv_imports i ON i.source_id=b.source_id
        JOIN sources s ON s.id=b.source_id
        WHERE s.place_id IN ({marks})
        GROUP BY i.source_id,i.camera_view,i.video_sha256,s.place_id,
                 b.camera_local_track_id
        UNION ALL
        SELECT 'frigate',NULL,e.camera,NULL,e.place_id,e.event_id,
               e.started_at,e.ended_at,NULL,e.score,e.binding_epoch_id
        FROM frigate_object_events e
        WHERE e.place_id IN ({marks})
    )
    SELECT * FROM scoped
    ORDER BY provider,COALESCE(source_id,0),camera_key,track_id
    LIMIT ? OFFSET ?"""
    return [dict(x) for x in c.execute(query,(*keys,*keys,limit,offset))]


def _project(row,allowed):
    source_id=row['source_id']
    if row['provider']=='frigate':
        key=_key('frigate',row['camera_key'],str(row['track_id']))
        return {
            'occurrence_key':key,'provider':'frigate',
            'evidence_kind':'upstream_anonymous_person_event',
            'camera_key':row['camera_key'],'source_id':None,
          'binding_epoch_id':row['binding_epoch_id'],
            'track_id':row['track_id'],'venue_id':row['venue_id'],
            'venue_name':allowed[row['venue_id']]['name'],
            'first_seen':row['first_seen'],'last_seen':row['last_seen'],
            'time_basis':'UTC_event_clock','unit':'utc_timestamp',
            'annotation_frames':None,'confidence':row['score'],
            'evidence_link':None,'subject_id':None,'identity_confirmed':False,
            'counts_as_unique_person':False,'counts_as_visit':False,
        }
    return {
        'occurrence_key':_key('caviar',f"{source_id}:{row['file_sha']}",
                               str(row['track_id'])),
        'provider':'caviar_ground_truth',
        'evidence_kind':'published_manual_local_tracking_annotation',
        'camera_key':row['camera_key'],'source_id':source_id,
        'binding_epoch_id':None,
        'track_id':str(row['track_id']),'venue_id':row['venue_id'],
        'venue_name':allowed[row['venue_id']]['name'],
        'first_seen':row['first_seen'],'last_seen':row['last_seen'],
        'time_basis':'archival_video_frame_number',
        'unit':'frame_index_1_based',
        'annotation_frames':row['annotation_frames'],
        'confidence':None,
        'evidence_link':f"/api/cctv/sources/{source_id}/activity",
        'subject_id':None,'identity_confirmed':False,
        'counts_as_unique_person':False,'counts_as_visit':False,
    }


def occurrences(c,allowed,*,limit=MAX_PAGE,offset=0):
    """Stable page of visible camera-local evidence, never a global sample."""
    if not 1<=limit<=MAX_PAGE or offset<0:
        raise ValueError('limit 1..200 and offset>=0 are required')
    fetched=_scoped_rows(c,allowed,limit=limit+1,offset=offset)
    return [_project(x,allowed) for x in fetched[:limit]],len(fetched)>limit


def iter_occurrences(c,allowed,*,batch=500):
    """Full authorized enumeration for QA lookups and honest summary counts.

    This is deliberately independent of the UI's first 200 results.
    """
    offset=0
    while True:
        page=_scoped_rows(c,allowed,limit=batch,offset=offset)
        for row in page:
            yield _project(row,allowed)
        if len(page)<batch:
            break
        offset+=len(page)


def occurrence_by_key(c,allowed,key):
    """Authorized key lookup spanning ALL pages, not just page 1.

    No submitted key can grant access to out-of-jurisdiction events because
    the SQL cursor is scoped before each page. Index-backed key projection
    may replace this sequential lookup for large production deployments.
    """
    for row in iter_occurrences(c,allowed):
        if row['occurrence_key']==key:
            return row
    return None


@router.get('/api/site/occurrences')
def occurrence_list(request:Request,limit:int=Query(100,ge=1,le=MAX_PAGE),
                    offset:int=Query(0,ge=0)):
    with _db() as c:
        allowed=_allowed(c,request)
        rows,has_more=occurrences(c,allowed,limit=limit,offset=offset)
        return {
            'mode':'camera_local_occurrence_only','occurrences':rows,
            'returned':len(rows),'truncated':has_more,
            'offset':offset,'next_offset':offset+len(rows) if has_more else None,
            'identity_verification':'not_performed',
            'site_visit_generation':'not_performed',
            'key_contract':'SHA256(provider, camera/source+file-revision, camera-local-track)',
            'note':'同机位同一目标连续数百帧仍是一条局部轨迹；跨机位局部轨迹不自动识别为同一个人。'
        }


@router.get('/api/site/occurrences/summary')
def occurrence_summary(request:Request,days:int|None=Query(default=None,ge=1,le=31)):
    """Aggregate per-source *track segments*, not faces or independent humans.

    CAVIAR frame numbers can NEVER enter Frigate's UTC-date trend window.
    """
    with _db() as c:
        allowed=_allowed(c,request)
        by={}
        start,end=monitoring.utc_day_window(days) if days is not None else (None,None)
        live_query="""SELECT camera,place_id,COUNT(*) AS local_tracks
            FROM frigate_object_events"""
        params=()
        if start is not None:
            live_query+=" WHERE started_at>=? AND started_at<?"
            params=(start.isoformat(),end.isoformat())
        live_query+=" GROUP BY camera,place_id ORDER BY camera"
        for r in c.execute(live_query,params):
            if r['place_id'] not in allowed:
                continue
            key=('frigate',r['camera'],r['place_id'])
            by[key]={'provider':'frigate','camera_key':r['camera'],
                     'venue_id':r['place_id'],
                     'venue_name':allowed[r['place_id']]['name'],
                     'local_occurrences':r['local_tracks'],
                     'annotated_frames':None,'time_basis':'UTC_event_clock'}
        for r in c.execute("""SELECT i.source_id,i.camera_view,s.place_id,
            COUNT(DISTINCT b.camera_local_track_id) AS local_tracks,
            COUNT(*) AS boxes
            FROM public_cctv_track_boxes b
            JOIN public_cctv_imports i ON i.source_id=b.source_id
            JOIN sources s ON s.id=i.source_id
            GROUP BY i.source_id,i.camera_view,s.place_id ORDER BY i.source_id"""):
            if r['place_id'] not in allowed:
                continue
            key=('caviar_ground_truth',str(r['source_id']),r['place_id'])
            by[key]={'provider':'caviar_ground_truth','camera_key':r['camera_view'],
                     'source_id':r['source_id'],'venue_id':r['place_id'],
                     'venue_name':allowed[r['place_id']]['name'],
                     'local_occurrences':r['local_tracks'],
                     'annotated_frames':r['boxes'],
                     'time_basis':'archival_video_frame_number'}
        rows=list(by.values())
        rows.sort(key=lambda x:(x['venue_id'],x['provider'],x['camera_key'],
                                str(x.get('source_id',''))))
        counts={}
        for r in rows:
            n=counts.setdefault(r['provider'],{'camera_local_occurrences':0,
                'cameras':0,'annotated_frames':0})
            n['camera_local_occurrences']+=r['local_occurrences']
            n['cameras']+=1
            n['annotated_frames']+=(r['annotated_frames'] or 0)
        return {'mode':'anonymous_local_tracks_per_camera',
                'window':({'start_utc':start.isoformat(),
                          'end_exclusive_utc':end.isoformat(),'days':days}
                           if start is not None else None),
                'frigate_time_scope':'UTC_calendar_days' if start is not None else 'all_time',
                'research_time_scope':'archival_clip_frames_all_time_not_calendar_days',
                'by_camera':rows,'by_provider':counts,'visible_cameras':len(rows),
                'scope':{'venues':[{'id':p['id'],'name':p['name']}
                                      for p in sorted(allowed.values(),key=lambda p:p['id'])]},
                'counts_unique_people':False,'counts_site_visits':False,
                'merged_across_cameras':False,'mixes_historical_with_realtime':False,
                'note':'Frigate局部目标事件可以按UTC自然日与监测对象计数核对；CAVIAR只有原片帧号，始终单独统计。局部事件不是独立人数或可信到访。'}
