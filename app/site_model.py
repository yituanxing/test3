"""Shared site/zone/source history and anonymous *evidence* projections.

Excel '场所' is a place; a school may contain many zones and cameras.
No camera-local track is ever a person ID. Historical attribution is valid
only after an observed binding was created, never guessed from the latest
source.place_id or a legacy backfill.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from app import access, admin

router = APIRouter()
_db = None
EPOCH_UNKNOWN = 'legacy_unknown_prior_location'


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


class ZoneCreate(BaseModel):
    place_id: int = Field(gt=0)
    name: str = Field(min_length=2,max_length=80)
    category: Literal['entrance','corridor','room','outdoor','other'] = 'other'


class ZoneState(BaseModel):
    active: bool


class BindingChange(BaseModel):
    place_id: int = Field(gt=0)
    zone_id: int | None = Field(default=None,gt=0)
    reason: str = Field(min_length=2,max_length=240)


def initialize(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS site_zones (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      place_id INTEGER NOT NULL REFERENCES places(id),
      name TEXT NOT NULL,
      category TEXT NOT NULL CHECK(category IN ('entrance','corridor','room','outdoor','other')),
      active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
      created_at TEXT NOT NULL,
      UNIQUE(place_id,name)
    );
    CREATE INDEX IF NOT EXISTS site_zones_by_place ON site_zones(place_id,id);
    CREATE TABLE IF NOT EXISTS source_site_bindings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_id INTEGER NOT NULL REFERENCES sources(id),
      place_id INTEGER NOT NULL REFERENCES places(id),
      zone_id INTEGER REFERENCES site_zones(id),
      valid_from TEXT NOT NULL,
      valid_until TEXT,
      reason TEXT NOT NULL,
      provenance TEXT NOT NULL CHECK(provenance IN ('explicit','legacy_backfill')),
      created_at TEXT NOT NULL,
      CHECK(valid_until IS NULL OR valid_until >= valid_from)
    );
    CREATE INDEX IF NOT EXISTS source_site_binding_history
      ON source_site_bindings(source_id,valid_from,id);
    CREATE UNIQUE INDEX IF NOT EXISTS source_site_one_current_binding
      ON source_site_bindings(source_id) WHERE valid_until IS NULL;
    CREATE TABLE IF NOT EXISTS frigate_site_binding_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      camera TEXT NOT NULL REFERENCES frigate_camera_bindings(camera),
      place_id INTEGER NOT NULL REFERENCES places(id),
      valid_from TEXT NOT NULL,
      valid_until TEXT,
      provenance TEXT NOT NULL CHECK(provenance IN ('explicit','legacy_backfill')),
      created_at TEXT NOT NULL,
      CHECK(valid_until IS NULL OR valid_until > valid_from)
    );
    CREATE INDEX IF NOT EXISTS frigate_binding_epoch_lookup
       ON frigate_site_binding_history(camera,valid_from,id);
    CREATE UNIQUE INDEX IF NOT EXISTS frigate_binding_one_current
       ON frigate_site_binding_history(camera) WHERE valid_until IS NULL;
    """)
    # Old source rows existed before this model. Baseline only *now*, not 1970:
    # for earlier events ownership is UNKNOWN, so no retroactive fake location.
    for row in c.execute("""SELECT s.id,s.place_id FROM sources s WHERE NOT EXISTS
           (SELECT 1 FROM source_site_bindings b WHERE b.source_id=s.id)""").fetchall():
        record_initial(c,row['id'],row['place_id'],provenance='legacy_backfill')
    for row in c.execute("""SELECT b.camera,b.place_id FROM frigate_camera_bindings b
         WHERE NOT EXISTS (SELECT 1 FROM frigate_site_binding_history h
                           WHERE h.camera=b.camera)""").fetchall():
        record_frigate_initial(c,row['camera'],row['place_id'],
                               provenance='legacy_backfill')


def _as_utc_microseconds(value):
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('摄像机事件必须有明确时区')
    return dt.astimezone(timezone.utc).isoformat(timespec='microseconds')


def record_frigate_initial(c,camera,place_id,*,provenance='explicit',at=None):
    """No backdating. Migrated bindings have UNKNOWN earlier physical history."""
    if c.execute('SELECT 1 FROM frigate_site_binding_history WHERE camera=?',
                 (camera,)).fetchone():
        return False
    stamp=_as_utc_microseconds(at) if at else now_utc()
    c.execute("""INSERT INTO frigate_site_binding_history
       (camera,place_id,valid_from,valid_until,provenance,created_at)
       VALUES(?,?,?,NULL,?,?)""",(camera,place_id,stamp,provenance,stamp))
    return True


def set_frigate_binding(c,camera,place_id,*,at=None):
    """Record monotone camera relocation without rewriting old epochs.

    Camera metadata and its history must share ONE SQLite transaction.
    The at parameter is for fixture setup; public API uses server time.
    """
    stamp=_as_utc_microseconds(at) if at else now_utc()
    current=c.execute("""SELECT * FROM frigate_site_binding_history
            WHERE camera=? AND valid_until IS NULL""",(camera,)).fetchone()
    original=c.execute('SELECT place_id FROM frigate_camera_bindings WHERE camera=?',
                       (camera,)).fetchone()
    if original is None:
        # Satisfy the history table's FK in the same pending transaction.
        # This is a brand-new explicit registration, NOT a legacy backfill.
        c.execute('INSERT INTO frigate_camera_bindings(camera,place_id) VALUES(?,?)',
                  (camera,place_id))
    if not current and original is not None:
        # The legacy pointer is only observed NOW; do not invent years of
        # ownership. One microsecond is enough to make the transition valid.
        legacy_start=(datetime.fromisoformat(stamp)-timedelta(microseconds=1)).isoformat(
            timespec='microseconds')
        record_frigate_initial(c,camera,original['place_id'],
                               provenance='legacy_backfill',at=legacy_start)
        current=c.execute("""SELECT * FROM frigate_site_binding_history
            WHERE camera=? AND valid_until IS NULL""",(camera,)).fetchone()
    if current and current['place_id']==place_id:
        return dict(current)
    if current:
        if stamp <= current['valid_from']:
            raise HTTPException(409,'新机位归属不得早于上一条绑定记录')
        c.execute("""UPDATE frigate_site_binding_history SET valid_until=?
                WHERE id=?""",(stamp,current['id']))
    cur=c.execute("""INSERT INTO frigate_site_binding_history
            (camera,place_id,valid_from,valid_until,provenance,created_at)
            VALUES(?,?,?,NULL,'explicit',?)""",(camera,place_id,stamp,stamp))
    return dict(c.execute('SELECT * FROM frigate_site_binding_history WHERE id=?',
                          (cur.lastrowid,)).fetchone())


def resolve_frigate_binding(c,camera,started_at):
    """Use event-time location; current assignment cannot backfill history."""
    instant=_as_utc_microseconds(started_at)
    row=c.execute("""SELECT * FROM frigate_site_binding_history
         WHERE camera=? AND valid_from<=?
           AND (valid_until IS NULL OR valid_until>?)
         ORDER BY valid_from DESC,id DESC LIMIT 1""",(camera,instant,instant)).fetchone()
    return dict(row) if row else None


def record_initial(c,source_id,place_id,*,provenance='explicit',at=None):
    exists=c.execute('SELECT 1 FROM source_site_bindings WHERE source_id=?',
                     (source_id,)).fetchone()
    if exists:
        return False
    moment=at or now_utc()
    c.execute("""INSERT INTO source_site_bindings
        (source_id,place_id,zone_id,valid_from,reason,provenance,created_at)
        VALUES(?,?,NULL,?,?,?,?)""",
        (source_id,place_id,moment,
         EPOCH_UNKNOWN if provenance=='legacy_backfill' else 'initial_source_registration',
         provenance,moment))
    return True


def _zone(c,place_id,zone_id):
    if zone_id is None:
        return
    row=c.execute('SELECT place_id,active FROM site_zones WHERE id=?',(zone_id,)).fetchone()
    if not row or row['place_id']!=place_id or not row['active']:
        raise HTTPException(422,'区域不存在、已停用或不属于所选场所')


def _current(c,source_id):
    row=c.execute("""SELECT * FROM source_site_bindings
        WHERE source_id=? AND valid_until IS NULL ORDER BY id DESC LIMIT 1""",
        (source_id,)).fetchone()
    return dict(row) if row else None


def change_binding(c,source_id,place_id,zone_id,reason,*,at=None):
    """Atomic interval transition; called by BOTH source CRUD and zone API.

    Client cannot set an arbitrary backdated clock: only caller's server time.
    Once recorded, historical source ownership is not rewritten.
    """
    _zone(c,place_id,zone_id)
    original=c.execute('SELECT place_id FROM sources WHERE id=?',(source_id,)).fetchone()
    if not original:
        raise HTTPException(404,'视频源不存在')
    current=_current(c,source_id)
    if current is None:
        record_initial(c,source_id,original['place_id'],provenance='legacy_backfill',at=at)
        current=_current(c,source_id)
    if current['place_id']==place_id and current['zone_id']==zone_id:
        return {**current,'unchanged':True}
    clock=at or now_utc()
    if clock<=current['valid_from']:
        raise HTTPException(409,'更新摄像机绑定必须晚于上一次绑定时间')
    c.execute('UPDATE source_site_bindings SET valid_until=? WHERE id=?',
              (clock,current['id']))
    added=c.execute("""INSERT INTO source_site_bindings
        (source_id,place_id,zone_id,valid_from,reason,provenance,created_at)
        VALUES(?,?,?,?,?,'explicit',?)""",
        (source_id,place_id,zone_id,clock,reason,clock))
    admin.audit(c,'camera_site_binding_update',f'source:{source_id}',
                {'binding_id':current['id'],'place_id':current['place_id'],
                 'zone_id':current['zone_id']},
                {'binding_id':added.lastrowid,'place_id':place_id,
                 'zone_id':zone_id,'reason':reason})
    return {**dict(c.execute('SELECT * FROM source_site_bindings WHERE id=?',
                             (added.lastrowid,)).fetchone()),'unchanged':False}


def resolve_binding(c,source_id,when):
    """No fallback to mutable sources.place_id for pre-binding timestamps."""
    instant=datetime.fromisoformat(when.replace('Z','+00:00'))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('UTC offset required')
    at=instant.astimezone(timezone.utc).isoformat(timespec='microseconds')
    row=c.execute("""SELECT * FROM source_site_bindings
       WHERE source_id=? AND valid_from<=? AND (valid_until IS NULL OR valid_until>?)
       ORDER BY valid_from DESC,id DESC LIMIT 1""",(source_id,at,at)).fetchone()
    return dict(row) if row else None


def register(app,db):
    global _db
    _db=db
    app.include_router(router)


def _editor(request):
    actor=access.principal(request)
    if actor is not None and actor['level']>2:
        raise HTTPException(403,'此操作仅允许州或县级维护人员')


@router.get('/api/site/zones')
def zone_list(request:Request,place_id:int|None=Query(default=None,gt=0)):
    with _db() as c:
        if place_id is not None:
            access.check_place(c,request,place_id)
        allowed={p['id'] for p in admin.place_rows(c,access.effective_region(c,request,None))}
        return {'zones':[{**dict(z),'active':bool(z['active'])}
             for z in c.execute('SELECT * FROM site_zones ORDER BY place_id,id')
             if z['place_id'] in allowed and (place_id is None or z['place_id']==place_id)],
             'mode':'site_inventory_no_person_identification'}


@router.post('/api/site/zones',status_code=201)
def create_zone(data:ZoneCreate,request:Request):
    _editor(request)
    with _db() as c:
        access.check_place(c,request,data.place_id)
        admin._active_place(c,data.place_id)
        name=data.name.strip()
        if len(name)<2:
            raise HTTPException(422,'区域名称至少2个字')
        try:
            cur=c.execute("""INSERT INTO site_zones
                 (place_id,name,category,created_at) VALUES(?,?,?,?)""",
                 (data.place_id,name,data.category,now_utc()))
        except __import__('sqlite3').IntegrityError as exc:
            raise HTTPException(409,'同一场所内部的场景名称不可重复') from exc
        result={'id':cur.lastrowid,**data.model_dump(),'name':name,'active':True}
        admin.audit(c,'site_zone_create',f'zone:{cur.lastrowid}',None,result)
        return result


@router.patch('/api/site/zones/{zone_id}/state')
def update_zone_state(zone_id:int,data:ZoneState,request:Request):
    _editor(request)
    with _db() as c:
        old=c.execute('SELECT * FROM site_zones WHERE id=?',(zone_id,)).fetchone()
        if not old:
            raise HTTPException(404,'场景/区域不存在')
        access.check_place(c,request,old['place_id'])
        if not data.active and c.execute("""SELECT 1 FROM source_site_bindings
            WHERE zone_id=? AND valid_until IS NULL LIMIT 1""",(zone_id,)).fetchone():
            raise HTTPException(409,'需要先解绑当前摄像机才能停用这个区域')
        c.execute('UPDATE site_zones SET active=? WHERE id=?',
                  (int(data.active),zone_id))
        admin.audit(c,'site_zone_state',f'zone:{zone_id}',
                    {'active':bool(old['active'])},{'active':data.active})
        return {'id':zone_id,'active':data.active}


@router.get('/api/site/sources/{source_id}/bindings')
def binding_history(source_id:int,request:Request):
    with _db() as c:
        access.check_source(c,request,source_id)
        records=[dict(r) for r in c.execute("""SELECT b.*,p.name AS place_name,
           z.name AS zone_name FROM source_site_bindings b
           JOIN places p ON p.id=b.place_id LEFT JOIN site_zones z ON z.id=b.zone_id
           WHERE b.source_id=? ORDER BY b.id""",(source_id,))]
        # A camera moved across counties cannot leak prior county's venue.
        for item in records:
            access.check_place(c,request,item['place_id'])
        return {'source_id':source_id,'bindings':records,
                'historical_source_location_verified_before_first_binding':False,
                'timestamp_semantics':'UTC interval [valid_from, valid_until); current source metadata is not historic fact'}


@router.put('/api/site/sources/{source_id}/binding')
def assign_binding(source_id:int,data:BindingChange,request:Request):
    _editor(request)
    with _db() as c:
        access.check_source(c,request,source_id)
        access.check_place(c,request,data.place_id)
        admin._active_place(c,data.place_id)
        if c.execute('SELECT 1 FROM public_cctv_imports WHERE source_id=?',
                     (source_id,)).fetchone():
            old=c.execute('SELECT place_id FROM sources WHERE id=?',(source_id,)).fetchone()
            if old and old['place_id']!=data.place_id:
                raise HTTPException(409,'固定研究数据源不能改绑为另一个场所')
        binding=change_binding(c,source_id,data.place_id,data.zone_id,data.reason)
        if not binding['unchanged'] and binding['place_id']!=c.execute(
              'SELECT place_id FROM sources WHERE id=?',(source_id,)).fetchone()['place_id']:
            c.execute('UPDATE sources SET place_id=? WHERE id=?',
                      (data.place_id,source_id))
        return binding


@router.get('/api/site/frigate/{camera}/bindings')
def frigate_binding_history(camera:str,request:Request):
    """Conservative historical camera location read: require EVERY venue.

    A camera transferred across counties cannot leak the previous county's
    address through its history, even to the current custodian.
    """
    with _db() as c:
        current=c.execute('SELECT place_id FROM frigate_camera_bindings WHERE camera=?',
                          (camera,)).fetchone()
        if not current:
            raise HTTPException(404,'Frigate摄像机不存在')
        access.check_place(c,request,current['place_id'])
        epochs=[dict(r) for r in c.execute("""SELECT h.*,p.name AS place_name
            FROM frigate_site_binding_history h JOIN places p ON p.id=h.place_id
            WHERE h.camera=? ORDER BY h.id""",(camera,))]
        for row in epochs:
            access.check_place(c,request,row['place_id'])
        return {'camera':camera,'epochs':epochs,
                'clock':'UTC half-open [valid_from,valid_until)',
                'prior_to_first_epoch':'unknown',
                'legacy_event_place_id_preserved':True}


@router.get('/api/site/anonymous-events')
def anonymous_events(request:Request,limit:int=Query(default=100,ge=1,le=300)):
    """Compatibility view over THE canonical camera-local read model.

    Avoid maintaining a second provider-specific parser with its own global
    LIMIT, identity semantics, or jurisdiction logic. Historic response field
    names remain available to existing clients; new integrations should use
    /api/site/occurrences and its offset-paged variant.
    """
    from app import occurrences
    with _db() as c:
        allowed=occurrences._allowed(c,request)
        # The legacy endpoint allowed up to 300 results. Fetch canonical
        # pages rather than reimplementing a competing SQL event query.
        rows=[]
        offset=0
        while len(rows)<limit+1:
            batch,more=occurrences.occurrences(
                c,allowed,limit=min(occurrences.MAX_PAGE,limit+1-len(rows)),
                offset=offset)
            rows.extend(batch)
            offset+=len(batch)
            if not more or not batch:
                break
        truncated=len(rows)>limit
        view=[]
        for x in rows[:limit]:
            is_caviar=x['provider']=='caviar_ground_truth'
            view.append({
                'provider':x['provider'],'source_id':x['source_id'],
                'camera':x['camera_key'],'local_track_key':x['track_id'],
                'place_id':x['venue_id'],
                # A current source.zone_id is NOT historic physical-location
                # evidence for a freely rewound public MPEG video.
                'zone_id':None,
                'time_basis':x['time_basis'],
                'start':x['first_seen'],'end':x['last_seen'],
                'annotation_boxes':x['annotation_frames'] if is_caviar else None,
                'evidence_origin':x['evidence_kind'],
                'occurrence_key':x['occurrence_key'],
                'subject_id':None,'unique_person_counted':False,
            })
        return {'mode':'anonymous_observation_only',
                'events':view,'total_returned':len(view),
                'limited':truncated,'identity_verified':False,
                'count_is_unique_people':False,
                'read_model':'/api/site/occurrences',
                'note':'兼容旧接口：实际使用同一个已授权事件投影；无身份去重、不能生成真人到访。'}
