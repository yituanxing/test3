"""Evidence-first scene storyboard for local *animated* video clips.

A cut is a possible shot boundary, NOT a recognized place or a character move.
Scenes are named by a human. Fictional character C### routes only derive from
reviewer-linked existing video sample IDs in the same file revision.
No lat/lon, face identity inference, or cross-video identity joins.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
import uuid
from urllib.parse import unquote
import re

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app import access, admin, anime_ontology

router = APIRouter()
_db = None
_revision = None
_upload_dir = None
MAX_UPLOAD_BYTES = 128 * 1024 * 1024


class SceneIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    start_ms: int = Field(ge=0, le=86400000)
    end_ms: int = Field(gt=0, le=86400000)
    note: str = Field(default='', max_length=300)


class CharacterIn(BaseModel):
    character_code: str = Field(pattern=r'^C[0-9]{3}$')
    sample_id: int = Field(gt=0)
    note: str = Field(default='人工对照该帧确认动画角色', min_length=2, max_length=300)


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def initialize(c):
    c.executescript("""
      CREATE TABLE IF NOT EXISTS anime_scene_segments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES sources(id),
        source_revision TEXT NOT NULL,
        label TEXT NOT NULL,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL,
        CHECK(start_ms>=0 AND end_ms>start_ms)
      );
      CREATE INDEX IF NOT EXISTS anime_scenes_source ON anime_scene_segments(source_id,start_ms);
      CREATE TABLE IF NOT EXISTS anime_character_reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        segment_id INTEGER NOT NULL REFERENCES anime_scene_segments(id),
        sample_id INTEGER NOT NULL REFERENCES samples(id),
        character_code TEXT NOT NULL,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(segment_id,sample_id,character_code)
      );
      CREATE INDEX IF NOT EXISTS anime_char_segment ON anime_character_reviews(segment_id,id);
    """)


def register(app, db, file_revision, upload_dir):
    global _db, _revision, _upload_dir
    _db, _revision, _upload_dir = db, file_revision, Path(upload_dir)
    app.include_router(router)


def _source(c, request, source_id):
    access.check_source(c, request, source_id)
    row = c.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone()
    if not row:
        raise HTTPException(404,'视频源不存在')
    if row['kind'] != 'file':
        raise HTTPException(422,'故事板目前只支持可重放的本地动画视频文件')
    if not Path(row['uri']).is_file():
        raise HTTPException(404,'本地视频文件不存在')
    return row


def _require_editor(request):
    actor = access.principal(request)
    if actor is not None and actor['level'] != 1:
        raise HTTPException(403,'只有州级管理员可标注演示动画场景')


def _file_rev(row):
    try:
        return _revision(row['uri'])
    except OSError as exc:
        raise HTTPException(409,'读取视频内容失败，无法确认场景标注所属文件版本') from exc


def _suggest_video(path, *, threshold, min_duration_ms, sample_stride, max_seconds):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise HTTPException(422,'视频文件无法解码')
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if not 0.01 < fps < 1000:
            raise HTTPException(422,'无法确认视频帧率；不猜测场景的实际时间轴')
        max_frames = min(int(fps*max_seconds), 7200)
        cuts = [0]
        strengths = []
        previous = None
        decoded = 0
        elapsed = 0
        for index in range(max_frames):
            ok, frame = capture.read()
            if not ok:
                break
            decoded += 1
            if index % sample_stride:
                continue
            small = cv2.resize(frame,(64,36),interpolation=cv2.INTER_AREA)
            if previous is not None:
                delta = float(np.mean(cv2.absdiff(small,previous)))
                time_ms = round(index*1000/fps)
                if delta >= threshold and time_ms - cuts[-1] >= min_duration_ms:
                    cuts.append(time_ms)
                    strengths.append(round(delta,2))
            previous=small
            elapsed=round((index+1)*1000/fps)
        if not decoded:
            raise HTTPException(422,'视频没有可解码画面')
        suggestions = []
        for i,start in enumerate(cuts):
            end = cuts[i+1] if i+1 < len(cuts) else elapsed
            if end <= start:
                continue
            suggestions.append({'start_ms':start,'end_ms':end,
                                'boundary_score':strengths[i-1] if i else None,
                                'label':f'镜头 {i+1}（待人工命名）'})
        return {'method':'sampled_mean_pixel_difference','decoded_frames':decoded,
                'fps':round(fps,3),'analyzed_ms':elapsed,'segments':suggestions,
                'complete_video':decoded<max_frames,
                'note':'仅检测像素变化候选剪辑点，不能识别真实或虚构地理位置，也不证明角色移动；快速运动可能误触发。'}
    finally:
        capture.release()


@router.post('/api/anime/scenes/uploads',status_code=201)
async def upload_local_clip(request:Request,place_id:int=Query(gt=0)):
    """Raw request-body upload: no multipart dependency and no arbitrary paths.

    Explicitly local demo scope: max 128 MiB, a bounded single-file upload,
    unique server filenames, permission checked BEFORE bytes are accepted.
    Video decoding is verified before DB registration; neither footage nor
    derived frames are part of the CI deliverable source archive.
    """
    _require_editor(request)
    with _db() as c:
        access.check_place(c,request,place_id)
        admin._active_place(c,place_id)
    client_name=unquote(request.headers.get('x-upload-name','clip.mp4'))
    ext=Path(client_name).suffix.lower()
    if ext not in {'.mp4','.mov','.mkv','.avi'}:
        raise HTTPException(422,'仅支持 mp4、mov、mkv、avi 动画视频')
    length=request.headers.get('content-length')
    if length:
        try:
            declared=int(length)
        except ValueError as exc:
            raise HTTPException(400,'文件长度不是整数') from exc
        if declared>MAX_UPLOAD_BYTES:
            raise HTTPException(413,'本地Demo单个动画视频上限128MiB')
    _upload_dir.mkdir(parents=True,exist_ok=True)
    final=_upload_dir/(uuid.uuid4().hex+ext)
    staged=final.with_name('pending-'+final.name)  # preserve video suffix for decoder
    count=0
    try:
        with staged.open('xb') as target:
            async for chunk in request.stream():
                count+=len(chunk)
                if count>MAX_UPLOAD_BYTES:
                    raise HTTPException(413,'本地Demo单个动画视频上限128MiB')
                target.write(chunk)
        if count<=0:
            raise HTTPException(422,'空文件不能作为视频源')
        capture=cv2.VideoCapture(str(staged))
        try:
            opened=capture.isOpened()
            decoded,frame=capture.read() if opened else (False,None)
        finally:
            capture.release()
        if not decoded:
            raise HTTPException(422,'已上传的内容不是可解码视频')
        os.replace(staged,final)
        with _db() as c:
            access.check_place(c,request,place_id)
            admin._active_place(c,place_id)
            display=(Path(client_name).stem.strip() or '动画样本')[:70]
            row=c.execute("""INSERT INTO sources(place_id,name,kind,uri)
               VALUES(?,?,?,?)""",(place_id,'上传动画 · '+display,'file',str(final)))
            admin.audit(c,'anime_video_uploaded',f"source:{row.lastrowid}",None,
                        {'place_id':place_id,'size_bytes':count,
                         'kind':'file','mode':'local_demo_clip'})
            return {'id':row.lastrowid,'place_id':place_id,'kind':'file',
                    'name':'上传动画 · '+display,'size_bytes':count,
                    'source_revision':_revision(str(final)),
                    'note':'本地上传成功。场景和角色仍需人工审阅，上传本身不证明自动识别。'}
    except Exception:
        staged.unlink(missing_ok=True)
        # Atomic video registration failure must not strand the uploaded file.
        final.unlink(missing_ok=True)
        raise


@router.get('/api/anime/scenes/sources/{source_id}/suggest')
def suggest(source_id:int,request:Request,
            threshold:float=Query(32,ge=5,le=200),
            min_duration_ms:int=Query(500,ge=100,le=20000),
            sample_stride:int=Query(4,ge=1,le=30),
            max_seconds:int=Query(45,ge=1,le=180)):
    with _db() as c:
        row=_source(c,request,source_id)
        rev=_file_rev(row)
        result=_suggest_video(row['uri'],threshold=threshold,
            min_duration_ms=min_duration_ms,sample_stride=sample_stride,
            max_seconds=max_seconds)
        return {'source_id':source_id,'source_name':row['name'],
                'source_revision':rev,'mode':'scene_cut_candidates_only',**result}


@router.post('/api/anime/scenes/sources/{source_id}/segments',status_code=201)
def add_segment(source_id:int,data:SceneIn,request:Request):
    _require_editor(request)
    if data.end_ms<=data.start_ms:
        raise HTTPException(422,'结束时间必须晚于开始')
    with _db() as c:
        source=_source(c,request,source_id)
        rev=_file_rev(source)
        overlapping=c.execute("""SELECT id FROM anime_scene_segments
            WHERE source_id=? AND source_revision=? AND start_ms < ? AND end_ms > ?
            LIMIT 1""",(source_id,rev,data.end_ms,data.start_ms)).fetchone()
        if overlapping:
            raise HTTPException(409,'该版本视频的镜头时间段不可重叠')
        cursor=c.execute("""INSERT INTO anime_scene_segments
          (source_id,source_revision,label,start_ms,end_ms,note,created_at)
          VALUES(?,?,?,?,?,?,?)""",
          (source_id,rev,data.label.strip(),data.start_ms,data.end_ms,
           data.note,_utc()))
        return {'id':cursor.lastrowid,'source_id':source_id,'source_revision':rev,
                **data.model_dump(),'origin':'manual_scene_annotation'}


@router.delete('/api/anime/scenes/segments/{segment_id}')
def delete_segment(segment_id:int,request:Request):
    _require_editor(request)
    with _db() as c:
        row=c.execute('SELECT source_id FROM anime_scene_segments WHERE id=?',(segment_id,)).fetchone()
        if not row:
            raise HTTPException(404,'镜头标注不存在')
        _source(c,request,row['source_id'])
        count=c.execute('SELECT COUNT(*) AS n FROM anime_character_reviews WHERE segment_id=?',
                        (segment_id,)).fetchone()['n']
        if count:
            raise HTTPException(409,'请先处理镜头内的人工角色证据，不直接级联删除')
        grouping=c.execute('SELECT 1 FROM anime_segment_group_reviews WHERE segment_id=? LIMIT 1',
                           (segment_id,)).fetchone()
        if grouping:
            raise HTTPException(409,'该镜头已有人工剧情场景归并历史，不可直接删除证据')
        c.execute('DELETE FROM anime_scene_segments WHERE id=?',(segment_id,))
        return {'deleted':segment_id}


@router.post('/api/anime/scenes/segments/{segment_id}/characters',status_code=201)
def attach_character(segment_id:int,data:CharacterIn,request:Request):
    _require_editor(request)
    with _db() as c:
        segment=c.execute('SELECT * FROM anime_scene_segments WHERE id=?',(segment_id,)).fetchone()
        if not segment:
            raise HTTPException(404,'镜头标注不存在')
        source=_source(c,request,segment['source_id'])
        if _file_rev(source)!=segment['source_revision']:
            raise HTTPException(409,'视频内容已变化，不能给过时的镜头绑定角色证据')
        sample=c.execute("""SELECT source_id,origin,video_offset_ms,source_revision
                FROM samples WHERE id=?""",(data.sample_id,)).fetchone()
        if not sample or sample['origin']!='video' or sample['source_id']!=segment['source_id']:
            raise HTTPException(422,'必须引用同一视频源已有的真实解码帧样本')
        if sample['source_revision'] != segment['source_revision']:
            raise HTTPException(409,'样本和场景的文件版本不一致')
        if sample['video_offset_ms'] is None or not (
                segment['start_ms'] <= sample['video_offset_ms'] < segment['end_ms']):
            raise HTTPException(422,'实际帧时间不属于该镜头标注区间')
        try:
            row=c.execute("""INSERT INTO anime_character_reviews
                (segment_id,sample_id,character_code,note,created_at)
                VALUES(?,?,?,?,?)""",(segment_id,data.sample_id,data.character_code,
                                      data.note,_utc()))
        except __import__('sqlite3').IntegrityError as exc:
            raise HTTPException(409,'相同镜头和帧上已有该虚构角色标注') from exc
        return {'id':row.lastrowid,'segment_id':segment_id,
                'character_code':data.character_code,'sample_id':data.sample_id,
                'mode':'manual_fictional_character_evidence'}


@router.get('/api/anime/scenes/sources/{source_id}/graph')
def graph(source_id:int,request:Request):
    with _db() as c:
        source=_source(c,request,source_id)
        revision=_file_rev(source)
        all_segments=[dict(x) for x in c.execute(
            'SELECT * FROM anime_scene_segments WHERE source_id=? ORDER BY start_ms,id',
            (source_id,))]
        segments=[s for s in all_segments if s['source_revision']==revision]
        cuts=[]
        labels={}
        for segment in segments:
            label=segment['label']
            labels[label]=labels.get(label,0)+1
            # Only samples that have a verified matching file revision and time.
            sample=c.execute("""SELECT COUNT(*) AS n FROM samples
              WHERE source_id=? AND origin='video' AND source_revision=?
              AND video_offset_ms>=? AND video_offset_ms<?""",
              (source_id,revision,segment['start_ms'],segment['end_ms'])).fetchone()
            segment['decoded_sample_count']=sample['n']
            segment['evidence_samples']=[dict(x) for x in c.execute("""SELECT id,video_offset_ms,frame_index
              FROM samples WHERE source_id=? AND origin='video' AND source_revision=?
              AND video_offset_ms>=? AND video_offset_ms<?
              ORDER BY video_offset_ms LIMIT 10""",
              (source_id,revision,segment['start_ms'],segment['end_ms']))]
            segment['mode']='manually_named_video_interval'
            cuts.append(segment)
        canonical=anime_ontology.enrich_graph(c,segments)
        transition_counts=defaultdict(int)
        for a,b in zip(segments,segments[1:]):
            if a['label']!=b['label']:
                transition_counts[(a['label'],b['label'])]+=1
        transitions=[{'from_scene':a,'to_scene':b,'cuts':n,'relation':'edit_sequence_only'}
                     for (a,b),n in sorted(transition_counts.items())]
        reviews=[dict(r) for r in c.execute("""SELECT r.id,r.segment_id,r.sample_id,
              r.character_code,r.note,r.created_at,s.video_offset_ms
              FROM anime_character_reviews r
              JOIN anime_scene_segments g ON r.segment_id=g.id
              JOIN samples s ON r.sample_id=s.id
              WHERE g.source_id=? AND g.source_revision=? AND s.source_revision=?
              ORDER BY s.video_offset_ms,r.id""",(source_id,revision,revision))]
        by_character=defaultdict(list)
        segment_map={x['id']:x for x in segments}
        for review in reviews:
            place=segment_map.get(review['segment_id'])
            if place is not None:
                by_character[review['character_code']].append({
                    'scene_label':place['label'],'segment_id':place['id'],
                    'story_venue_id':place.get('canonical_venue_id'),
                    'story_venue_name':place.get('canonical_venue_name'),
                    'canonical_scene_group_id':place.get('canonical_scene_group_id'),
                    'sample_id':review['sample_id'],'at_ms':review['video_offset_ms'],
                    'evidence':'human_tag_of_actual_decoded_frame'})
        trajectories={key:rows for key,rows in sorted(by_character.items())}
        return {'mode':'manually_annotated_anime_storyboard',
                'source_id':source_id,'source_name':source['name'],
                'source_revision':revision,
                'has_stale_segments':len(segments)!=len(all_segments),
                'stale_segment_count':len(all_segments)-len(segments),
                'nodes':[{'label':label,'segments':count,'coords':'not_available'}
                         for label,count in sorted(labels.items())],
                'segments':cuts,'edges':transitions,'characters':trajectories,
                **canonical,'scene_vs_source':'源视频所属场所仅用于文件权限，不能作为整集动画所有镜头的叙事场所',
                'note':'这是视频镜头剪辑顺序图，而不是现实GPS地图；C###路径只来自逐帧人工证据，不是自动角色跟踪。'}
