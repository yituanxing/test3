"""Manual ontology for cartoons: video source != physical/story location.

A scene group is a repeatable narrative setting (e.g. school A classroom),
and a venue is the larger Excel '场所' concept (e.g. school A). One video can
visit MANY venues. No inferred movement, face identity or GPS coordinates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import sqlite3
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app import access

router = APIRouter()
_db = None
_file_revision = None


class VenueIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class GroupIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class GroupDecision(BaseModel):
    scene_group_id: int = Field(gt=0)
    reason: str = Field(min_length=2, max_length=300)


def initialize(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS anime_story_venues(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS anime_story_scene_groups(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      venue_id INTEGER NOT NULL REFERENCES anime_story_venues(id),
      name TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(venue_id,name)
    );
    CREATE TABLE IF NOT EXISTS anime_segment_group_reviews(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      segment_id INTEGER NOT NULL REFERENCES anime_scene_segments(id),
      scene_group_id INTEGER NOT NULL REFERENCES anime_story_scene_groups(id),
      reason TEXT NOT NULL,
      reviewed_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS anime_segment_group_rev_idx
      ON anime_segment_group_reviews(segment_id,id);
    """)


def register(app, db, file_revision):
    global _db, _file_revision
    _db, _file_revision = db, file_revision
    app.include_router(router)


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _state_editor(request):
    actor=access.principal(request)
    if actor is not None and actor['level']!=1:
        raise HTTPException(403,'只有州级管理员可维护动画演示场景词典')


def _existing_venue(c,venue_id):
    row=c.execute('SELECT * FROM anime_story_venues WHERE id=?',(venue_id,)).fetchone()
    if not row:
        raise HTTPException(404,'剧情场所不存在')
    return dict(row)


@router.get('/api/anime/ontology')
def list_ontology(request:Request):
    # The library is global to the local demo. Lower-region users should not
    # see names derived from otherwise inaccessible video sources.
    _state_editor(request)
    with _db() as c:
        venues=[dict(row) for row in c.execute('SELECT * FROM anime_story_venues ORDER BY id')]
        groups=[dict(row) for row in c.execute(
            'SELECT g.*,v.name AS venue_name FROM anime_story_scene_groups g '
            'JOIN anime_story_venues v ON v.id=g.venue_id ORDER BY g.id')]
    return {'venues':venues,'scene_groups':groups,'mode':'manually_curated_storyworld'}


@router.post('/api/anime/ontology/venues',status_code=201)
def create_venue(data:VenueIn,request:Request):
    _state_editor(request)
    with _db() as c:
        name=data.name.strip()
        if len(name)<2:
            raise HTTPException(422,'剧情场所名称至少两个字符')
        try:
            cur=c.execute('INSERT INTO anime_story_venues(name,created_at) VALUES(?,?)',
                          (name,_utc()))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409,'同一剧中场所已存在，建议复用已有场所') from exc
        return {'id':cur.lastrowid,'name':name,'mode':'manual_story_venue'}


@router.post('/api/anime/ontology/venues/{venue_id}/scenes',status_code=201)
def create_scene_group(venue_id:int,data:GroupIn,request:Request):
    _state_editor(request)
    with _db() as c:
        venue=_existing_venue(c,venue_id)
        name=data.name.strip()
        if len(name)<2:
            raise HTTPException(422,'场景名称至少两个字符')
        try:
            cur=c.execute('''INSERT INTO anime_story_scene_groups
                (venue_id,name,created_at) VALUES(?,?,?)''',
                (venue_id,name,_utc()))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409,'该剧情场所下已有同名场景，请选现有场景组') from exc
        return {'id':cur.lastrowid,'venue_id':venue_id,'venue_name':venue['name'],
                'name':name,'mode':'manual_scene_group'}


@router.put('/api/anime/ontology/segments/{segment_id}/group')
def assign_group(segment_id:int,data:GroupDecision,request:Request):
    _state_editor(request)
    with _db() as c:
        # Label only the current version of a locally decoded video, never
        # silently reassign a future replacement at the same source ID.
        row=c.execute('''SELECT s.source_id,s.source_revision,v.uri,v.kind
            FROM anime_scene_segments s JOIN sources v ON v.id=s.source_id
            WHERE s.id=?''',(segment_id,)).fetchone()
        if not row:
            raise HTTPException(404,'镜头标注不存在')
        access.check_source(c,request,row['source_id'])
        if row['kind']!='file':
            raise HTTPException(422,'非本地文件无法进行剧情场景确认')
        try:
            current=_file_revision(row['uri'])
        except OSError as exc:
            raise HTTPException(409,'视频文件不存在或已不可访问') from exc
        if current!=row['source_revision']:
            raise HTTPException(409,'原动画视频已变更，不能沿用旧镜头的分类')
        group=c.execute('''SELECT g.id,g.name,g.venue_id,v.name AS venue_name
            FROM anime_story_scene_groups g JOIN anime_story_venues v
              ON v.id=g.venue_id WHERE g.id=?''',
              (data.scene_group_id,)).fetchone()
        if not group:
            raise HTTPException(404,'剧情场景组不存在')
        prior=c.execute('''SELECT scene_group_id FROM anime_segment_group_reviews
            WHERE segment_id=? ORDER BY id DESC LIMIT 1''',(segment_id,)).fetchone()
        cur=c.execute('''INSERT INTO anime_segment_group_reviews
          (segment_id,scene_group_id,reason,reviewed_at) VALUES(?,?,?,?)''',
          (segment_id,group['id'],data.reason,_utc()))
        return {'segment_id':segment_id,'review_id':cur.lastrowid,
                'scene_group_id':group['id'],'scene_group_name':group['name'],
                'story_venue_id':group['venue_id'],'story_venue_name':group['venue_name'],
                'previous_scene_group_id':prior['scene_group_id'] if prior else None,
                'mode':'audited_human_scene_classification'}


def enrich_graph(c, segments: list[dict]) -> dict:
    """Separate shot edit graph from canonical setting/venue aggregation.

    No locations are guessed from shot captions or source.place_id. The
    returned transitions explicitly are VIDEO EDITS, never character routes.
    """
    if not segments:
        return {'canonical_venues':[],'canonical_scenes':[],
                'canonical_scene_transitions':[],
                'cross_venue_edit_transitions':[],
                'unclassified_segments':0}
    ids=[x['id'] for x in segments]
    marks={row['segment_id']:dict(row) for row in c.execute('''
      SELECT g.segment_id,sg.id AS scene_group_id,sg.name AS scene_group_name,
             v.id AS story_venue_id,v.name AS story_venue_name
      FROM anime_segment_group_reviews g
      JOIN (SELECT segment_id,MAX(id) AS last_id FROM anime_segment_group_reviews
            GROUP BY segment_id) newest
      ON newest.segment_id=g.segment_id AND newest.last_id=g.id
      JOIN anime_story_scene_groups sg ON sg.id=g.scene_group_id
      JOIN anime_story_venues v ON v.id=sg.venue_id
    ''')}
    venues=defaultdict(lambda:{'segments':0,'scene_group_ids':set()})
    scene_counts=Counter()
    for segment in segments:
        mark=marks.get(segment['id'])
        if not mark:
            segment.update({'canonical_scene_group_id':None,'canonical_scene_name':None,
                            'canonical_venue_id':None,'canonical_venue_name':None,
                            'manual_classification':False})
            continue
        segment.update({'canonical_scene_group_id':mark['scene_group_id'],
                        'canonical_scene_name':mark['scene_group_name'],
                        'canonical_venue_id':mark['story_venue_id'],
                        'canonical_venue_name':mark['story_venue_name'],
                        'manual_classification':True})
        vid=mark['story_venue_id']
        venues[(vid,mark['story_venue_name'])]['segments']+=1
        venues[(vid,mark['story_venue_name'])]['scene_group_ids'].add(mark['scene_group_id'])
        scene_counts[(mark['scene_group_id'],mark['scene_group_name'],vid)]+=1
    # Includes ONLY adjacent classified shots; sequence != actual movement.
    transitions=Counter()
    across=Counter()
    for a,b in zip(segments,segments[1:]):
        aid,bid=a['canonical_scene_group_id'],b['canonical_scene_group_id']
        if aid is None or bid is None or aid==bid:
            continue
        transitions[(aid,bid)]+=1
        if a['canonical_venue_id']!=b['canonical_venue_id']:
            across[(a['canonical_venue_id'],b['canonical_venue_id'])]+=1
    return {
        'canonical_venues':[{'id':k[0],'name':k[1],
                             'segments':v['segments'],'scene_group_count':len(v['scene_group_ids']),
                             'coordinates':None}
                            for k,v in sorted(venues.items())],
        'canonical_scenes':[{'id':k[0],'name':k[1],'story_venue_id':k[2],
                             'segments':num} for k,num in sorted(scene_counts.items())],
        'canonical_scene_transitions':[{'from_scene_group_id':a,'to_scene_group_id':b,
            'shot_cuts':num,'relation':'edit_sequence_only_not_a_route'}
                for (a,b),num in sorted(transitions.items())],
        'cross_venue_edit_transitions':[{'from_venue_id':a,'to_venue_id':b,
            'shot_cuts':num,'relation':'edit_sequence_only_not_a_visit'}
                for (a,b),num in sorted(across.items())],
        'unclassified_segments':sum(x['canonical_scene_group_id'] is None for x in segments),
    }
