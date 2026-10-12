"""Four-tier fictional administrative regions and operational video-source management.

This is local-only DEMO infrastructure: no geolocation of persons, no identification.
The data migration preserves existing observations and binds legacy places to an
explicitly fictional leaf region; original place ids never change.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal
import sqlite3
import json
from datetime import datetime,timezone

LEVEL_NAMES = ('州', '县市', '街道', '社区')
DEMO_NAMES = ('演示州', '演示县市', '演示街道', '演示社区')
router = APIRouter()
_get_db = None


def initialize(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS regions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL CHECK(length(trim(name)) > 0),
          parent_id INTEGER REFERENCES regions(id),
          level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 4),
          UNIQUE(parent_id,name)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS region_root_unique ON regions(name) WHERE parent_id IS NULL;
        CREATE TABLE IF NOT EXISTS admin_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation TEXT NOT NULL,
          target TEXT NOT NULL,
          old_value TEXT,
          new_value TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
    ''')
    leaf = demo_leaf(conn)
    fields = {x['name'] for x in conn.execute('PRAGMA table_info(places)')}
    if 'region_id' not in fields:
        conn.execute('ALTER TABLE places ADD COLUMN region_id INTEGER REFERENCES regions(id)')
    if 'active' not in fields:
        conn.execute('ALTER TABLE places ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))')
    conn.execute('UPDATE places SET region_id=? WHERE region_id IS NULL',(leaf,))
    conn.execute('CREATE INDEX IF NOT EXISTS place_by_region ON places(region_id)')
    fields = {x['name'] for x in conn.execute('PRAGMA table_info(sources)')}
    if 'enabled' not in fields:
        conn.execute('ALTER TABLE sources ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))')


def audit(conn,operation,target,old,new):
    # Never duplicate potentially credential-bearing RTSP URI values in the audit.
    def sanitized(value):
        if isinstance(value,dict):
            return {k:('[REDACTED]' if k=='uri' else sanitized(v)) for k,v in value.items()}
        return value
    conn.execute('''INSERT INTO admin_audit(operation,target,old_value,new_value,created_at)
        VALUES(?,?,?,?,?)''',
        (operation,target,
         json.dumps(sanitized(old),ensure_ascii=False,sort_keys=True) if old is not None else None,
         json.dumps(sanitized(new),ensure_ascii=False,sort_keys=True),
         datetime.now(timezone.utc).isoformat(timespec='seconds')))


def demo_leaf(conn):
    parent=None
    for level,name in enumerate(DEMO_NAMES,1):
        if parent is None:
            conn.execute('INSERT OR IGNORE INTO regions(name,parent_id,level) VALUES(?,NULL,?)',(name,level))
            row=conn.execute('SELECT id FROM regions WHERE parent_id IS NULL AND name=?',(name,)).fetchone()
        else:
            conn.execute('INSERT OR IGNORE INTO regions(name,parent_id,level) VALUES(?,?,?)',(name,parent,level))
            row=conn.execute('SELECT id FROM regions WHERE parent_id=? AND name=?',(parent,name)).fetchone()
        parent=row['id']
    return parent


def regions_with_paths(conn):
    rows=[dict(r) for r in conn.execute('SELECT id,name,parent_id,level FROM regions ORDER BY level,id')]
    path={}
    for r in rows:
        path[r['id']] = (path.get(r['parent_id'],'')+' / ' if r['parent_id'] else '')+r['name']
        r['path']=path[r['id']]
        r['level_name']=LEVEL_NAMES[r['level']-1]
    return rows


def subtree_ids(conn, region_id):
    if region_id is None:
        return None
    if not conn.execute('SELECT 1 FROM regions WHERE id=?',(region_id,)).fetchone():
        raise HTTPException(404,'行政区域不存在')
    return {r['id'] for r in conn.execute('''
      WITH RECURSIVE subtree(id) AS (
        SELECT id FROM regions WHERE id=?
        UNION ALL SELECT r.id FROM regions r JOIN subtree t ON r.parent_id=t.id
      ) SELECT id FROM subtree''',(region_id,))}


def place_rows(conn, region_id=None):
    region_ids=subtree_ids(conn,region_id)
    paths={r['id']:r['path'] for r in regions_with_paths(conn)}
    rows=[dict(r) for r in conn.execute('SELECT * FROM places ORDER BY id')]
    for r in rows:
        r['active']=bool(r['active'])
        r['region_path']=paths.get(r['region_id'],'（未指定）')
    return [r for r in rows if region_ids is None or r['region_id'] in region_ids]


def _active_place(conn, place_id):
    place=conn.execute('SELECT id,active FROM places WHERE id=?',(place_id,)).fetchone()
    if not place:
        raise HTTPException(404,'场所不存在')
    if not place['active']:
        raise HTTPException(409,'场所已停用')


def _validate_leaf(conn, region_id):
    row=conn.execute('SELECT level FROM regions WHERE id=?',(region_id,)).fetchone()
    if not row:
        raise HTTPException(404,'所选行政区域不存在')
    if row['level']!=4:
        raise HTTPException(422,'场所必须归属第4级社区')


def validate_source(kind, uri):
    if kind=='camera':
        try:
            n=int(uri)
            if not 0<=n<=10:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400,'本机摄像头请填写索引0至10') from exc
    if kind=='rtsp':
        # Network-stream source compatibility: MPEG-TS over local UDP for an offline
        # functional test. This is *not* RTSP, despite the legacy enum name.
        if uri.startswith('udp://'):
            from urllib.parse import urlsplit
            try:
                target = urlsplit(uri)
                if target.hostname not in ('127.0.0.1', 'localhost') or not target.port:
                    raise ValueError('only local UDP streams permitted')
            except ValueError as exc:
                raise HTTPException(400,'仅允许 udp://127.0.0.1:端口 的本地网络测试流') from exc
        elif not uri.startswith(('rtsp://','rtsps://')):
            raise HTTPException(400,'网络流地址须为rtsp://、rtsps://或本地udp://127.0.0.1:端口')


class RegionIn(BaseModel):
    name: str=Field(min_length=1,max_length=100)
    parent_id: int|None=Field(default=None,ge=1)


class PlaceUpdate(BaseModel):
    name: str=Field(min_length=1,max_length=100)
    region_id: int=Field(ge=1)
    active: bool=True


class SourceUpdate(BaseModel):
    name: str=Field(min_length=1,max_length=100)
    place_id: int=Field(ge=1)
    kind: Literal['file','camera','rtsp']
    uri: str=Field(min_length=1,max_length=1000)
    enabled: bool=True


class SourceState(BaseModel):
    enabled: bool


def register(app,get_db):
    global _get_db
    _get_db=get_db
    app.include_router(router)


@router.get('/api/regions')
def get_regions():
    with _get_db() as c:
        return regions_with_paths(c)


@router.post('/api/regions')
def add_region(data: RegionIn):
    name=data.name.strip()
    if not name:
        raise HTTPException(422,'区域名称不能为空')
    with _get_db() as c:
        if data.parent_id is None:
            level=1
        else:
            parent=c.execute('SELECT level FROM regions WHERE id=?',(data.parent_id,)).fetchone()
            if not parent:
                raise HTTPException(404,'上级区域不存在')
            if parent['level']>=4:
                raise HTTPException(422,'社区不能再添加下级区域')
            level=parent['level']+1
        try:
            row=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,?)',
                         (name,data.parent_id,level))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409,'同一上级下区域名称重复') from exc
        result={'id':row.lastrowid,'parent_id':data.parent_id,'name':name,'level':level}
        audit(c,'region_create',f"region:{result['id']}",None,result)
        return result


@router.put('/api/places/{place_id}')
def update_place(place_id:int,data:PlaceUpdate):
    with _get_db() as c:
        old=c.execute('SELECT * FROM places WHERE id=?',(place_id,)).fetchone()
        if not old:
            raise HTTPException(404,'场所不存在')
        _validate_leaf(c,data.region_id)
        name=data.name.strip()
        if not name:
            raise HTTPException(422,'场所名称不能为空')
        try:
            c.execute('UPDATE places SET name=?,region_id=?,active=? WHERE id=?',
                     (name,data.region_id,int(data.active),place_id))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409,'场所名称重复') from exc
        result={'id':place_id,'name':name,'region_id':data.region_id,'active':data.active}
        audit(c,'place_update',f'place:{place_id}',dict(old),result)
    return result


@router.put('/api/sources/{source_id}')
def update_source(source_id:int,data:SourceUpdate):
    validate_source(data.kind,data.uri)
    with _get_db() as c:
        old=c.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone()
        if not old:
            raise HTTPException(404,'视频源不存在')
        if data.enabled:
            _active_place(c,data.place_id)
        elif not c.execute('SELECT 1 FROM places WHERE id=?',(data.place_id,)).fetchone():
            raise HTTPException(404,'场所不存在')
        name=data.name.strip()
        if not name:
            raise HTTPException(422,'视频源名称不能为空')
        c.execute('UPDATE sources SET name=?,place_id=?,kind=?,uri=?,enabled=? WHERE id=?',
                  (name,data.place_id,data.kind,data.uri,int(data.enabled),source_id))
        result={'id':source_id,**data.model_dump(),'name':name}
        audit(c,'source_update',f'source:{source_id}',dict(old),result)
    return result


@router.patch('/api/sources/{source_id}/state')
def source_state(source_id:int,data:SourceState):
    with _get_db() as c:
        src=c.execute('SELECT place_id,enabled FROM sources WHERE id=?',(source_id,)).fetchone()
        if not src:
            raise HTTPException(404,'视频源不存在')
        if data.enabled:
            _active_place(c,src['place_id'])
        c.execute('UPDATE sources SET enabled=? WHERE id=?',(int(data.enabled),source_id))
        audit(c,'source_state',f'source:{source_id}',{'enabled':bool(src['enabled'])},
              {'enabled':data.enabled})
    return {'id':source_id,'enabled':data.enabled}


@router.get('/api/admin/audit')
def audit_log(limit: int=Query(50,ge=1,le=200)):
    with _get_db() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM admin_audit ORDER BY id DESC LIMIT ?',(limit,))]
