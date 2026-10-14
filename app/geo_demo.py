"""Geographic venue pins and synthetic P### visit sequence demo.

Map dots for fictional venues are never claimed to be real installations.
Connecting successive venue points is a chronological visual aid, NOT a road
trace or a verified real person's movement. The Frigate/EPFL anonymous
evidence stores are deliberately not imported here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pathlib import Path

from app import access, admin, insights

router = APIRouter()
_get_db = None
_analyze = None

WARN = ('地图只表示已录入或完全模拟的场所点位；P###是假身份。'
        '站点间顺序连线只是示意，不代表道路/GPS轨迹、摄像机覆盖，'
        '不得用于现实人员定位、跨机身份关联或风险处置。')


def initialize(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS demo_place_geo (
          place_id INTEGER PRIMARY KEY REFERENCES places(id),
          lat REAL NOT NULL CHECK(lat BETWEEN -90 AND 90),
          lon REAL NOT NULL CHECK(lon BETWEEN -180 AND 180),
          provenance TEXT NOT NULL CHECK(
            provenance IN ('fictional_seed','operator_entered_unverified')),
          note TEXT NOT NULL,
          updated_by INTEGER,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS demo_geo_audit (
          id INTEGER PRIMARY KEY,
          operation TEXT NOT NULL,
          place_id INTEGER,
          actor_id INTEGER,
          created_at TEXT NOT NULL
        );
    """)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _places(c,request,region_id):
    scope=access.effective_region(c,request,region_id)
    allowed=admin.place_rows(c,scope)
    coords={r['place_id']:dict(r) for r in c.execute('SELECT * FROM demo_place_geo')}
    return [{**p,'geo':(
        {key:coords[p['id']][key] for key in ('lat','lon','provenance','note','updated_at')}
        if p['id'] in coords else None)} for p in allowed]


class GeoInput(BaseModel):
    lat: float = Field(ge=-90,le=90,allow_inf_nan=False)
    lon: float = Field(ge=-180,le=180,allow_inf_nan=False)
    note: str = Field(min_length=2,max_length=200)


def _admin(request):
    actor=getattr(request.state,'principal',None)
    if not actor or actor['level']!=1:
        raise HTTPException(403,'只有州级管理员可以设置场所地图坐标')
    return actor


def register(app,get_db,analyze):
    global _get_db,_analyze
    _get_db=get_db
    _analyze=analyze
    app.include_router(router)


@router.get('/geo')
def screen():
    return FileResponse(Path(__file__).resolve().parents[1]/'static'/'geo.html')


@router.get('/api/geo/places')
def points(request:Request,region_id:int|None=Query(None,ge=1)):
    with _get_db() as c:
        places=_places(c,request,region_id)
        return {'mode':'synthetic_demo_geography','places':places,
                'positioned':sum(p['geo'] is not None for p in places),
                'disclaimer':WARN}


@router.put('/api/geo/places/{place_id}')
def assign(place_id:int,body:GeoInput,request:Request):
    actor=_admin(request)
    with _get_db() as c:
        if not c.execute('SELECT 1 FROM places WHERE id=?',(place_id,)).fetchone():
            raise HTTPException(404,'场所不存在')
        c.execute("""INSERT INTO demo_place_geo
          (place_id,lat,lon,provenance,note,updated_by,updated_at)
          VALUES (?,?,?,'operator_entered_unverified',?,?,?)
          ON CONFLICT(place_id) DO UPDATE SET lat=excluded.lat,lon=excluded.lon,
          provenance=excluded.provenance,note=excluded.note,
          updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
          (place_id,body.lat,body.lon,body.note.strip(),actor['id'],_now()))
        c.execute('INSERT INTO demo_geo_audit(operation,place_id,actor_id,created_at) VALUES(?,?,?,?)',
                  ('manual_coordinate_set',place_id,actor['id'],_now()))
    return {'place_id':place_id,'saved':True,
            'provenance':'operator_entered_unverified',
            'note':'人工填写，不代表经过外部位置核验'}


@router.post('/api/geo/demo/seed')
def seed(request:Request):
    actor=_admin(request)
    with _get_db() as c:
        # Coordinates occupy a tiny arbitrary area; positions are explicitly
        # fictional even though they look like real WGS84 coordinates.
        places=c.execute('SELECT id FROM places WHERE active=1 ORDER BY id').fetchall()
        added=0
        for i,row in enumerate(places):
            lat=43.815 + (i//5)*0.008 + (i%2)*0.003
            lon=87.593 + (i%5)*0.012
            added+=c.execute("""INSERT OR IGNORE INTO demo_place_geo
                (place_id,lat,lon,provenance,note,updated_by,updated_at)
                VALUES (?,?,?,'fictional_seed',?,?,?)""",
                (row['id'],lat,lon,'虚构地图演示点：无现场测绘依据',actor['id'],_now())).rowcount
        c.execute('INSERT INTO demo_geo_audit(operation,place_id,actor_id,created_at) VALUES(?,?,?,?)',
                  (f'seed_fictional_coordinates:{added}',None,actor['id'],_now()))
    return {'inserted':added,'position_type':'fictional_seed',
            'disclaimer':WARN}


@router.get('/api/geo/track')
def track(request:Request,person:str=Query(...,pattern=r'^P[0-9]{3}$'),
          days:int=Query(30,ge=1,le=365),
          region_id:int|None=Query(None,ge=1)):
    if not re.fullmatch(r'P[0-9]{3}',person):
        raise HTTPException(422,'仅允许虚构P###人员编号')
    with _get_db() as c:
        visible=_places(c,request,region_id)
        allowed={p['id']:p for p in visible}
        analysis=_analyze(c)
        first,last=insights._window(analysis,days)
        visits=sorted((v for v in analysis['visits']
                       if v['person']==person and v['place_id'] in allowed
                       and first<=insights.stamp(v['started_at']).date()<=last),
                      key=lambda v:(insights.stamp(v['started_at']),
                                    v['place_id'],str(v.get('observation_ids',[]))))
        out=[]
        no_position=0
        for v in visits:
            place=allowed[v['place_id']]
            point=place['geo']
            if not point:
                no_position+=1
                continue
            out.append({'sequence':len(out)+1,'person':person,
                'place_id':place['id'],'place_name':place['name'],
                'lat':point['lat'],'lon':point['lon'],
                'coordinate_provenance':point['provenance'],
                'started_at':v['started_at'],'ended_at':v['ended_at'],
                'synthetic_observation_ids':v['observation_ids']})
    return {'mode':'synthetic_only','person':person,'days':days,
            'events':out,'total_visible_visits':len(visits),
            'visits_without_coordinates':no_position,'disclaimer':WARN,
            'visualization':'chronological_venue_points_not_real_path'}
