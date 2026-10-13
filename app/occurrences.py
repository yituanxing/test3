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
from app import access, admin

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


def occurrences(c,allowed,*,limit=MAX_PAGE):
    """Return deterministic, bounded, traceable LOCAL observations only.

    The provider-local ID is intentionally NOT joined to another camera's ID.
    The video-local and UTC times are separate typed fields.
    """
    if not 1 <= limit <= MAX_PAGE:
        raise ValueError('limit must be 1..200')
    out=[]
    for r in c.execute("""SELECT camera,event_id,place_id,started_at,ended_at,
           score,has_snapshot FROM frigate_object_events
           ORDER BY started_at DESC,camera,event_id LIMIT 1500"""):
        if r['place_id'] not in allowed:
            continue
        row=dict(r)
        camera=row['camera']
        out.append({
          'occurrence_key':_key('frigate',camera,row['event_id']),
          'provider':'frigate','evidence_kind':'upstream_anonymous_person_event',
          'camera_key':camera,'source_id':None,
          'track_id':row['event_id'],'venue_id':row['place_id'],
          'venue_name':allowed[row['place_id']]['name'],
          'first_seen':row['started_at'],'last_seen':row['ended_at'],
          'time_basis':'UTC_event_clock','unit':'utc_timestamp',
          'annotation_frames':None,'confidence':row['score'],
          'evidence_link':None,
          'subject_id':None,'identity_confirmed':False,
          'counts_as_unique_person':False,'counts_as_visit':False})
    for r in c.execute("""SELECT i.source_id,i.camera_view,i.video_sha256,
            s.place_id,b.camera_local_track_id,
            MIN(b.frame_number) AS first_frame,
            MAX(b.frame_number) AS last_frame,COUNT(*) AS boxes
         FROM public_cctv_track_boxes b
         JOIN public_cctv_imports i ON b.source_id=i.source_id
         JOIN sources s ON s.id=b.source_id
         GROUP BY i.source_id,i.camera_view,i.video_sha256,
             s.place_id,b.camera_local_track_id
         ORDER BY i.source_id,b.camera_local_track_id LIMIT 1500"""):
        if r['place_id'] not in allowed:
            continue
        row=dict(r)
        # Track key MUST include source and original file digest, so equal
        # local IDs in a second camera/video can never collide.
        out.append({
          'occurrence_key':_key('caviar',f"{row['source_id']}:{row['video_sha256']}",
                                str(row['camera_local_track_id'])),
          'provider':'caviar_ground_truth',
          'evidence_kind':'published_manual_local_tracking_annotation',
          'camera_key':row['camera_view'],'source_id':row['source_id'],
          'track_id':str(row['camera_local_track_id']),
          'venue_id':row['place_id'],
          'venue_name':allowed[row['place_id']]['name'],
          'first_seen':row['first_frame'],'last_seen':row['last_frame'],
          'time_basis':'archival_video_frame_number',
          'unit':'frame_index_1_based','annotation_frames':row['boxes'],
          'confidence':None,
          'evidence_link':f"/api/cctv/sources/{row['source_id']}/activity",
          'subject_id':None,'identity_confirmed':False,
          'counts_as_unique_person':False,'counts_as_visit':False})
    out.sort(key=lambda x:(x['provider'],str(x['source_id'] or ''),x['camera_key'],x['track_id']))
    return out[:limit],len(out)>limit


@router.get('/api/site/occurrences')
def occurrence_list(request:Request,limit:int=Query(100,ge=1,le=MAX_PAGE)):
    with _db() as c:
        allowed=_allowed(c,request)
        rows,has_more=occurrences(c,allowed,limit=limit)
        return {
            'mode':'camera_local_occurrence_only','occurrences':rows,
            'returned':len(rows),'truncated':has_more,
            'identity_verification':'not_performed',
            'site_visit_generation':'not_performed',
            'key_contract':'SHA256(provider, camera/source+file-revision, camera-local-track)',
            'note':'同机位同一目标连续数百帧仍是一条局部轨迹；跨机位局部轨迹不自动识别为同一个人。'
        }


@router.get('/api/site/occurrences/summary')
def occurrence_summary(request:Request):
    """Aggregate per-source *track segments*, not faces or independent humans.

    CAVIAR frame numbers can NEVER enter Frigate's UTC-date trend window.
    """
    with _db() as c:
        allowed=_allowed(c,request)
        by={}
        for r in c.execute("""SELECT camera,place_id,COUNT(*) AS local_tracks
            FROM frigate_object_events GROUP BY camera,place_id ORDER BY camera"""):
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
                'by_camera':rows,'by_provider':counts,'visible_cameras':len(rows),
                'scope':{'venues':[{'id':p['id'],'name':p['name']}
                                      for p in sorted(allowed.values(),key=lambda p:p['id'])]},
                'counts_unique_people':False,'counts_site_visits':False,
                'merged_across_cameras':False,'mixes_historical_with_realtime':False,
                'note':'摄像机局部轨迹数不等于全场独立人数；历史公开人体框不计入实时人数及人员底库。'}
