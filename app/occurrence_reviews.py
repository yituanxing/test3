"""Append-only QA adjudication of *anonymous camera-local* detections.

A QA verdict NEVER supplies a real-person identity, merged camera identity,
site visit or risk finding. Reviews are keyed by deterministic local-track
keys and source-evidence content signatures. Changes invalidate old verdicts
without erasing either the original observation or review history.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app import access, admin, occurrences

router=APIRouter()
_db=None
VALID_KEY=re.compile(r'^[0-9a-f]{24}$')


class QualityReviewIn(BaseModel):
    verdict: Literal['valid_local_target','false_positive','needs_review']
    reason: str=Field(min_length=5,max_length=400)


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def initialize(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS anonymous_occurrence_reviews(
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       occurrence_key TEXT NOT NULL,
       provider TEXT NOT NULL,
       verdict TEXT NOT NULL CHECK(verdict IN (
           'valid_local_target','false_positive','needs_review')),
       reason TEXT NOT NULL,
       evidence_signature TEXT NOT NULL,
       reviewer_id INTEGER NOT NULL REFERENCES auth_users(id),
       reviewed_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS occurrence_reviews_latest
       ON anonymous_occurrence_reviews(occurrence_key,id);
    """)


def register(app,db):
    global _db
    _db=db
    app.include_router(router)


def _allowed(c,request):
    return occurrences._allowed(c,request)


def _resolve(c,request,occurrence_key):
    if not VALID_KEY.fullmatch(occurrence_key):
        raise HTTPException(422,'不是合法的匿名局部事件编号')
    # Lookup is scoped by SQL *before* paging and traverses the whole
    # authorized event set; page 201 is as reviewable as page 1.
    allowed=_allowed(c,request)
    row=occurrences.occurrence_by_key(c,allowed,occurrence_key)
    if row is None:
        raise HTTPException(404,'事件不存在或不在当前辖区')
    return row


def _signature(c,row):
    """Hash the underlying ORIGINAL evidence, not reviewer-entered assertions.

    Hash all camera-local annotated boxes, avoiding a false fresh status
    when frame positions change but min/max and box-count stay the same.
    """
    if row['provider']=='caviar_ground_truth':
        source=c.execute("""SELECT video_sha256 FROM public_cctv_imports
             WHERE source_id=?""",(row['source_id'],)).fetchone()
        frames=[tuple(v) for v in c.execute("""SELECT frame_number,x,y,width,height
            FROM public_cctv_track_boxes
            WHERE source_id=? AND camera_local_track_id=?
            ORDER BY frame_number""",(row['source_id'],row['track_id']))]
        extra={'video_sha256':source['video_sha256'] if source else None,'boxes':frames}
    elif row['provider']=='frigate':
        ev=c.execute("""SELECT started_at,ended_at,score,has_snapshot,updated_at,
                     place_id,label
             FROM frigate_object_events WHERE camera=? AND event_id=?""",
             (row['camera_key'],row['track_id'])).fetchone()
        extra={'upstream':dict(ev) if ev else None}
    else:
        raise ValueError('Unsupported provider in local occurrence signature')
    payload={
      'contract':'anonymous_camera_local_quality_v1',
      'evidence':row,
      'upstream':extra,
    }
    return sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,
                            separators=(',',':')).encode('utf-8')).hexdigest()


def _history(c,key):
    return [dict(r) for r in c.execute("""SELECT id,verdict,reason,evidence_signature,
           reviewer_id,reviewed_at FROM anonymous_occurrence_reviews
           WHERE occurrence_key=? ORDER BY id""",(key,))]


def _state(c,row):
    signature=_signature(c,row)
    history=_history(c,row['occurrence_key'])
    latest=history[-1] if history else None
    stale=bool(latest and latest['evidence_signature']!=signature)
    return {'occurrence_key':row['occurrence_key'],
            'provider':row['provider'],
            'quality_status':('not_reviewed' if latest is None else
                              'stale_review' if stale else latest['verdict']),
            'latest_review_id':latest['id'] if latest else None,
            'review_stale':stale,
            'evidence_signature':signature,
            'reviews':history,
            'identity_confirmed':False,'count_as_unique_person':False,
            'valid_site_visit':False,
            'message':'质检仅确认相机内匿名目标证据质量，绝不确认此目标的现实身份或跨机轨迹。'}


@router.get('/api/site/occurrences/{occurrence_key}/quality')
def get_quality(occurrence_key:str,request:Request):
    with _db() as c:
        row=_resolve(c,request,occurrence_key)
        return _state(c,row)


@router.post('/api/site/occurrences/{occurrence_key}/quality',status_code=201)
def review_quality(occurrence_key:str,data:QualityReviewIn,request:Request):
    actor=access.principal(request)
    if actor is None or actor['level']>2:
        raise HTTPException(403,'只有州/县级可审核匿名目标质量')
    if len(data.reason.strip())<5:
        raise HTTPException(422,'审核依据至少5个字符')
    with _db() as c:
        row=_resolve(c,request,occurrence_key)
        sig=_signature(c,row)
        cur=c.execute("""INSERT INTO anonymous_occurrence_reviews
          (occurrence_key,provider,verdict,reason,evidence_signature,reviewer_id,reviewed_at)
          VALUES(?,?,?,?,?,?,?)""",(occurrence_key,row['provider'],data.verdict,
              data.reason.strip(),sig,actor['id'],_utc()))
        admin.audit(c,'anonymous_occurrence_quality_review',
                    f"occurrence:{occurrence_key}",None,
                    {'id':cur.lastrowid,'provider':row['provider'],
                     'verdict':data.verdict,'scope_place_id':row['venue_id'],
                     'reviewer_id':actor['id'],'reason':data.reason.strip(),
                     'evidence_signature':sig})
        result=_state(c,row)
        result['created_review_id']=cur.lastrowid
        return result


@router.get('/api/site/quality/summary')
def quality_summary(request:Request):
    """Current QA counts only. Does NOT count people, visits or biometric IDs."""
    with _db() as c:
        rows=occurrences.iter_occurrences(c,_allowed(c,request))
        counts={'not_reviewed':0,'valid_local_target':0,'false_positive':0,
                'needs_review':0,'stale_review':0}
        scanned=0
        for row in rows:
            state=_state(c,row)
            counts[state['quality_status']]+=1
            scanned+=1
        return {'mode':'anonymous_evidence_quality_only',
                'counts':counts,'camera_local_events_scanned':scanned,
                'scan_bounded':False,
                'counts_real_people':False,'counts_site_visits':False,
                'reviewed_is_verified_identity':False,
                'note':'仅审核局部目标是否为合理人形目标，不确认真人身份；修改源证据后旧审核标记为过时。'}
