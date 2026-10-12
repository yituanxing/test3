"""Minimal, local-only real video ingestion -> detection -> durable events -> reviews -> stats.

Identity recognition and cross-person tracking are deliberately out of scope here.
Demo records are always clearly labeled as simulated.
"""
import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import cv2
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from app.detector import choose_detector, detector_info
from app.media_probe import probe_video
from app.frigate_bridge import parse_person_event
from app.business import analyze, DEFAULT_CONFIG, RULE_NAMES
from app.monitoring import overview as monitoring_overview
from app import admin, base_registry, insights, access
import httpx
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv('ZD_DATA_DIR', str(ROOT / 'data')))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / 'demo.sqlite3'
STATIC = ROOT / 'static' / 'index.html'
PORTAL_STATIC = ROOT / 'static' / 'portal.html'
BUSINESS_STATIC = ROOT / 'static' / 'business.html'
MONITOR_STATIC = ROOT / 'static' / 'monitor.html'
ADMIN_STATIC = ROOT / 'static' / 'admin.html'
INSIGHTS_STATIC = ROOT / 'static' / 'insights.html'
LOGIN_STATIC = ROOT / 'static' / 'login.html'


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def setup_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS places (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id INTEGER NOT NULL REFERENCES places(id),
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('file','camera','rtsp')),
            uri TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER REFERENCES sources(id),
            place_id INTEGER NOT NULL REFERENCES places(id),
            origin TEXT NOT NULL CHECK(origin IN ('video','synthetic')),
            ingested_at TEXT NOT NULL,
            video_offset_ms INTEGER,
            frame_index INTEGER,
            detected_faces INTEGER NOT NULL CHECK(detected_faces >= 0),
            detector TEXT NOT NULL DEFAULT 'legacy'
        );
        CREATE INDEX IF NOT EXISTS samples_place ON samples(place_id, id);
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id INTEGER NOT NULL REFERENCES samples(id),
            corrected_faces INTEGER NOT NULL CHECK(corrected_faces >= 0),
            reason TEXT NOT NULL,
            reviewed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS frigate_camera_bindings (
            camera TEXT PRIMARY KEY,
            place_id INTEGER NOT NULL REFERENCES places(id)
        );
        CREATE TABLE IF NOT EXISTS frigate_object_events (
            event_id TEXT PRIMARY KEY,
            camera TEXT NOT NULL REFERENCES frigate_camera_bindings(camera),
            place_id INTEGER NOT NULL REFERENCES places(id),
            label TEXT NOT NULL CHECK(label='person'),
            started_at TEXT NOT NULL,
            ended_at TEXT,
            score REAL,
            has_snapshot INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS frigate_place_date ON frigate_object_events(place_id,started_at);
        CREATE TABLE IF NOT EXISTS synthetic_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            occurrence_key TEXT,
            person_label TEXT NOT NULL,
            place_id INTEGER NOT NULL REFERENCES places(id),
            observed_at TEXT NOT NULL,
            origin TEXT NOT NULL CHECK(origin='synthetic_only')
        );
        CREATE TABLE IF NOT EXISTS synthetic_observation_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER NOT NULL REFERENCES synthetic_observations(id),
            excluded INTEGER NOT NULL CHECK(excluded IN (0,1)),
            corrected_person TEXT,
            reason TEXT NOT NULL,
            reviewed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS so_reviews_by_obs ON synthetic_observation_reviews(observation_id,id);
        CREATE TABLE IF NOT EXISTS synthetic_alert_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            evidence_signature TEXT
        );
        CREATE INDEX IF NOT EXISTS sa_reviews_by_key ON synthetic_alert_reviews(alert_key,id);
        CREATE TABLE IF NOT EXISTS synthetic_settings (
            id INTEGER PRIMARY KEY CHECK(id=1),
            json_value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS synthetic_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            int_value INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO settings(key, int_value) VALUES ('face_frame_threshold', 2);
        ''')
        # Backward-compatible migration for existing V0.1 user data.
        cols = [r['name'] for r in c.execute('PRAGMA table_info(samples)')]
        if 'detector' not in cols:
            c.execute("ALTER TABLE samples ADD COLUMN detector TEXT NOT NULL DEFAULT 'legacy'")
        # V1 observation event semantics: old rows keep NULL, meaning each
        # individual event_key was one explicit appearance event.
        observation_cols = [r['name'] for r in c.execute('PRAGMA table_info(synthetic_observations)')]
        if 'occurrence_key' not in observation_cols:
            c.execute('ALTER TABLE synthetic_observations ADD COLUMN occurrence_key TEXT')
        review_cols = [r['name'] for r in c.execute('PRAGMA table_info(synthetic_alert_reviews)')]
        if 'evidence_signature' not in review_cols:
            c.execute('ALTER TABLE synthetic_alert_reviews ADD COLUMN evidence_signature TEXT')
        c.execute("INSERT OR IGNORE INTO places (name) VALUES ('测试场所 A')")
        c.execute("INSERT OR IGNORE INTO places (name) VALUES ('测试场所 B')")
        bundled_video = ROOT / 'samples' / 'test-pattern.mp4'
        if bundled_video.is_file():
            place_id = c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
            if not c.execute("SELECT 1 FROM sources WHERE name='内置测试视频（无真人）'").fetchone():
                c.execute('INSERT INTO sources(place_id,name,kind,uri) VALUES(?,?,?,?)',
                          (place_id, '内置测试视频（无真人）', 'file', str(bundled_video)))
        admin.initialize(c)
        base_registry.initialize(c)
        access.initialize(c)


setup_db()
app = FastAPI(title='ZD Business Pipeline Demo', version='1.1.0')
admin.register(app,db)
base_registry.register(app,db)
access.register(app,db)


@app.middleware('http')
async def enforce_role_scope(request: Request, call_next):
    """Default-deny API gateway; region filters are additionally imposed by endpoints.

    Old regression tests may explicitly enable the compatibility mode, which is
    ONLY effective inside an active pytest test. Never used by the running server.
    """
    path = request.url.path
    if path.startswith('/api/') and path not in ('/api/health','/api/auth/login'):
        legacy_test = (os.environ.get('ZD_AUTH_TEST_COMPAT') == '1'
                       and bool(os.environ.get('PYTEST_CURRENT_TEST')))
        if not legacy_test:
            try:
                with db() as c:
                    actor = access.current(c, request.headers.get('authorization'))
                if not path.startswith('/api/auth/') and not access.permitted(actor,request.method,path):
                    raise HTTPException(403,'当前角色无权操作此接口')
                request.state.principal = actor
            except HTTPException as error:
                return JSONResponse({'detail':error.detail}, status_code=error.status_code,
                                    headers=error.headers)
    return await call_next(request)


@app.get('/login')
def login_page():
    return FileResponse(LOGIN_STATIC)


class PlaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    region_id: int | None = Field(default=None,ge=1)


class SourceIn(BaseModel):
    place_id: int
    name: str = Field(min_length=1, max_length=100)
    kind: Literal['file', 'camera', 'rtsp']
    uri: str = Field(min_length=1, max_length=1000)


class ReviewIn(BaseModel):
    corrected_faces: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1, max_length=500)


class RuleIn(BaseModel):
    face_frame_threshold: int = Field(ge=1, le=100)


@app.get('/')
def home():
    return FileResponse(STATIC)


@app.get('/portal')
def portal_home():
    return FileResponse(PORTAL_STATIC)


@app.get('/business')
def business_home():
    return FileResponse(BUSINESS_STATIC)


@app.get('/monitor')
def monitor_home():
    return FileResponse(MONITOR_STATIC)


@app.get('/admin')
def admin_home():
    return FileResponse(ADMIN_STATIC)


@app.get('/insights')
def insights_home():
    return FileResponse(INSIGHTS_STATIC)


@app.get('/api/monitoring/overview')
def get_monitoring_overview(request:Request, days: int = Query(7, ge=1, le=31), region_id: int | None = Query(None,ge=1)):
    with db() as c:
        region_id=access.effective_region(c,request,region_id)
        return monitoring_overview(c, days=days,region_id=region_id,
                      allowed_region_ids=admin.subtree_ids(c,region_id))


@app.get('/api/health')
def health():
    return {'status': 'ok', 'opencv': cv2.__version__, 'detector': detector_info(), 'db': str(DB)}


@app.get('/api/places')
def places(request:Request, region_id: int | None = Query(None,ge=1)):
    with db() as c:
        return admin.place_rows(c,access.effective_region(c,request,region_id))


@app.post('/api/places')
def add_place(data: PlaceIn,request:Request):
    name=data.name.strip()
    if not name:
        raise HTTPException(422,'场所名称不能为空')
    try:
        with db() as c:
            region_id=data.region_id or admin.demo_leaf(c) # legacy quick-start compatibility
            admin._validate_leaf(c,region_id)
            access.effective_region(c,request,region_id)
            cursor=c.execute('INSERT INTO places(name,region_id) VALUES(?,?)',(name,region_id))
            result={'id':cursor.lastrowid,'name':name,'region_id':region_id,'active':True}
            admin.audit(c,'place_create',f"place:{result['id']}",None,result)
            return result
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409,'场所名称重复') from exc


@app.get('/api/sources')
def sources(request:Request):
    with db() as c:
        allowed={p['id'] for p in admin.place_rows(c,access.effective_region(c,request,None))}
        return [{**dict(r), 'uri': ('[REDACTED]' if access.principal(request)
                                            and access.principal(request)['level']>=2 else r['uri'])}
                for r in c.execute('''
            SELECT s.*, p.name AS place_name, p.active AS place_active FROM sources s JOIN places p ON p.id=s.place_id ORDER BY s.id DESC
        ''') if r['place_id'] in allowed]


@app.post('/api/sources')
def add_source(data: SourceIn,request:Request):
    admin.validate_source(data.kind,data.uri)
    with db() as c:
        admin._active_place(c,data.place_id)
        access.check_place(c,request,data.place_id)
        cursor = c.execute('INSERT INTO sources(place_id,name,kind,uri) VALUES (?,?,?,?)',
                           (data.place_id, data.name, data.kind, data.uri))
        result={'id': cursor.lastrowid, **data.model_dump()}
        admin.audit(c,'source_create',f"source:{result['id']}",None,result)
        return result


@app.get('/api/sources/{source_id}/probe')
def probe_source(source_id: int):
    with db() as c:
        src = c.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
        if not src:
            raise HTTPException(404, '视频源不存在')
        src = dict(src)
        if not src['enabled']:
            raise HTTPException(409,'视频源已停用')
        admin._active_place(c,src['place_id'])
    if src['kind'] == 'camera':
        raise HTTPException(400, 'USB摄像头请直接分析；FFprobe不支持设备索引0')
    if src['kind'] == 'file' and not Path(src['uri']).is_file():
        raise HTTPException(400, '文件不存在（输入路径是后端机器的路径）')
    try:
        return {'source_id': source_id, **probe_video(src['uri'])}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post('/api/sources/{source_id}/process')
def process_source(source_id: int, max_frames: int = Query(90, ge=1, le=600),
                   sample_every: int = Query(10, ge=1, le=60)):
    with db() as c:
        row = c.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
        if not row:
            raise HTTPException(404, '视频源不存在')
        source = dict(row)
        if not source['enabled']:
            raise HTTPException(409,'视频源已停用')
        admin._active_place(c,source['place_id'])
    if source['kind'] == 'file' and not Path(source['uri']).is_file():
        raise HTTPException(400, '视频文件路径不存在：需填写服务端机器上的路径')
    capture_arg = int(source['uri']) if source['kind'] == 'camera' else source['uri']
    cap = cv2.VideoCapture(capture_arg)
    if not cap.isOpened():
        cap.release()
        raise HTTPException(400, '视频无法打开：检查摄像头权限、视频文件路径或RTSP流状态')
    try:
        detector = choose_detector()
    except Exception as err:
        cap.release()
        raise HTTPException(500, f'检测器初始化失败：{err}') from err
    read_count, sampled, faces_total = 0, 0, 0
    detections = []
    try:
        while read_count < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            read_count += 1
            if (read_count - 1) % sample_every:
                continue
            # Intentionally genuine CPU inference; no caching, no pre-filled results.
            count = detector.detect_count(frame)
            sampled += 1
            faces_total += count
            video_offset_ms = round(cap.get(cv2.CAP_PROP_POS_MSEC)) if source['kind'] == 'file' else None
            detections.append((source_id, source['place_id'], 'video', utcnow(),
                               video_offset_ms, read_count, count, detector.name))
    finally:
        cap.release()
    if not read_count:
        raise HTTPException(400, '视频已打开，但未读到帧')
    with db() as c:
        c.executemany('''INSERT INTO samples(source_id,place_id,origin,ingested_at,video_offset_ms,frame_index,detected_faces,detector)
                      VALUES (?,?,?,?,?,?,?,?)''', detections)
    return {'source_id': source_id, 'frames_decoded': read_count,
            'frames_analyzed': sampled, 'face_detections': faces_total, 'detector': detector.name,
            'note': '检测次数不是去重人数；未执行身份识别。'}


@app.post('/api/demo/seed')
def seed_test_events():
    """Known fake observations for predictable downstream tests, not video detections."""
    rows = []
    for place_id, counts in [(1, [0, 1, 2, 3, 4, 2, 0]), (2, [1, 0, 1, 1, 3, 0])]:
        for i, cnt in enumerate(counts):
            rows.append((None, place_id, 'synthetic', utcnow(), i * 1000, i, cnt, 'synthetic'))
    with db() as c:
        # Upsert known demo places, in case DB was recreated with different IDs.
        ids = []
        for name in ['测试场所 A', '测试场所 B']:
            c.execute('INSERT OR IGNORE INTO places(name) VALUES (?)', (name,))
            ids.append(c.execute('SELECT id FROM places WHERE name=?', (name,)).fetchone()['id'])
        rows = [(a, ids[0] if b == 1 else ids[1], *rest) for a,b,*rest in rows]
        c.executemany('''INSERT INTO samples(source_id,place_id,origin,ingested_at,video_offset_ms,frame_index,detected_faces,detector)
                      VALUES (?,?,?,?,?,?,?,?)''', rows)
    return {'inserted': len(rows), 'origin': 'synthetic', 'warning': '这批数据用于规则测试，不是视频AI识别结果。'}


@app.get('/api/rules')
def rules():
    with db() as c:
        return {'face_frame_threshold': c.execute("SELECT int_value FROM settings WHERE key='face_frame_threshold'").fetchone()[0]}


@app.put('/api/rules')
def save_rules(data: RuleIn):
    with db() as c:
        c.execute("UPDATE settings SET int_value=? WHERE key='face_frame_threshold'", (data.face_frame_threshold,))
    return {'face_frame_threshold': data.face_frame_threshold, 'recomputed': True}


@app.get('/api/events')
def events(limit: int = Query(50, ge=1, le=300)):
    with db() as c:
        rows = c.execute('''
           SELECT s.*, p.name AS place_name,
             (SELECT corrected_faces FROM reviews r WHERE r.sample_id=s.id ORDER BY r.id DESC LIMIT 1) AS reviewed_faces
           FROM samples s JOIN places p ON p.id=s.place_id ORDER BY s.id DESC LIMIT ?
        ''', (limit,)).fetchall()
        return [dict(r) for r in rows]


@app.post('/api/events/{event_id}/review')
def review(event_id: int, data: ReviewIn):
    with db() as c:
        if not c.execute('SELECT 1 FROM samples WHERE id=?', (event_id,)).fetchone():
            raise HTTPException(404, '记录不存在')
        cur = c.execute('INSERT INTO reviews(sample_id,corrected_faces,reason,reviewed_at) VALUES (?,?,?,?)',
                        (event_id, data.corrected_faces, data.reason, utcnow()))
        return {'review_id': cur.lastrowid, 'event_id': event_id, 'original_unchanged': True}


@app.get('/api/reviews')
def reviews():
    with db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM reviews ORDER BY id DESC LIMIT 100')]


@app.get('/api/summary')
def summary():
    with db() as c:
        threshold = c.execute("SELECT int_value FROM settings WHERE key='face_frame_threshold'").fetchone()[0]
        rows = c.execute('''
            SELECT p.id AS place_id, p.name AS place_name, COUNT(s.id) AS sampled_frames,
            COALESCE(SUM(s.detected_faces),0) AS model_face_detections,
            COALESCE(SUM(COALESCE(
                  (SELECT corrected_faces FROM reviews r WHERE r.sample_id=s.id ORDER BY r.id DESC LIMIT 1),
                  s.detected_faces)),0) AS effective_face_detections,
            COALESCE(SUM(CASE WHEN COALESCE(
                  (SELECT corrected_faces FROM reviews r WHERE r.sample_id=s.id ORDER BY r.id DESC LIMIT 1),
                  s.detected_faces) >= ? THEN 1 ELSE 0 END),0) AS threshold_hit_frames,
            COALESCE(SUM(CASE WHEN s.origin='video' THEN 1 ELSE 0 END),0) AS real_video_samples,
            COALESCE(SUM(CASE WHEN s.origin='synthetic' THEN 1 ELSE 0 END),0) AS simulated_samples
            FROM places p LEFT JOIN samples s ON p.id=s.place_id
            GROUP BY p.id, p.name ORDER BY p.id
        ''', (threshold,)).fetchall()
        return {'threshold': threshold, 'places': [dict(r) for r in rows],
                'note': '按帧检测到的人脸次数，不代表独立人员、到访次数或身份关系；规则为单帧数量演示阈值。'}

class FrigateCameraBinding(BaseModel):
    camera: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    place_id: int = Field(ge=1)


@app.post('/api/frigate/cameras')
def bind_frigate_camera(data: FrigateCameraBinding):
    """Explicit upstream camera -> local place mapping. No remote URL from requests."""
    with db() as c:
        admin._active_place(c,data.place_id)
        old=c.execute('SELECT place_id FROM frigate_camera_bindings WHERE camera=?',
                      (data.camera,)).fetchone()
        c.execute('''INSERT INTO frigate_camera_bindings(camera,place_id) VALUES(?,?)
                   ON CONFLICT(camera) DO UPDATE SET place_id=excluded.place_id''',
                  (data.camera, data.place_id))
        admin.audit(c,'frigate_binding',f'camera:{data.camera}',
                   {'place_id':old['place_id']} if old else None,data.model_dump())
    return data.model_dump()


@app.get('/api/frigate/cameras')
def list_frigate_cameras(request:Request):
    with db() as c:
        allowed={p['id'] for p in admin.place_rows(c,access.effective_region(c,request,None))}
        return [dict(r) for r in c.execute('''SELECT b.camera, b.place_id, p.name AS place_name
            FROM frigate_camera_bindings b JOIN places p ON p.id=b.place_id ORDER BY b.camera''')
            if r['place_id'] in allowed]


@app.post('/api/frigate/sync')
def sync_frigate(limit: int = Query(100, ge=1, le=500)):
    """Read local upstream Frigate events; record anonymous detection facts only.

    Environment ZD_FRIGATE_API is server-configured and never user supplied;
    default is the unauthenticated, LOOPBACK-BOUND local Frigate API port.
    """
    base = os.getenv('ZD_FRIGATE_API', 'http://127.0.0.1:5000').rstrip('/')
    try:
        response = httpx.get(base + '/api/events', params={'limit':limit,'label':'person'}, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f'Frigate服务不可达或响应无效：{type(exc).__name__}') from exc
    if not isinstance(payload, list):
        raise HTTPException(502, 'Frigate events API 应返回列表')
    inserted, updated, skipped = 0, 0, 0
    with db() as c:
        bindings = {r['camera']: r['place_id'] for r in c.execute('''
          SELECT b.camera,b.place_id FROM frigate_camera_bindings b
          JOIN places p ON p.id=b.place_id WHERE p.active=1''')}
        for item in payload:
            event = parse_person_event(item)
            if event is None or event.camera not in bindings:
                skipped += 1
                continue
            exists = c.execute('SELECT 1 FROM frigate_object_events WHERE event_id=?', (event.event_id,)).fetchone()
            c.execute('''INSERT INTO frigate_object_events
              (event_id,camera,place_id,label,started_at,ended_at,score,has_snapshot,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(event_id) DO UPDATE SET
                 camera=excluded.camera,place_id=excluded.place_id,
                 started_at=excluded.started_at,ended_at=excluded.ended_at,
                 score=excluded.score,has_snapshot=excluded.has_snapshot,
                 updated_at=excluded.updated_at''',
              (event.event_id,event.camera,bindings[event.camera],event.label,event.started_at,
               event.ended_at,event.score,int(event.has_snapshot),utcnow()))
            updated += int(exists is not None)
            inserted += int(exists is None)
    return {'inserted': inserted, 'updated': updated, 'skipped': skipped,
            'note': 'Frigate person 目标事件不是经身份去重的独立人数，也不是人脸身份识别。'}


@app.get('/api/frigate/events')
def list_frigate_events(request:Request, limit: int = Query(100, ge=1, le=500),
                        region_id: int | None = Query(None,ge=1)):
    with db() as c:
        region_id=access.effective_region(c,request,region_id)
        subtree=admin.subtree_ids(c,region_id)
        if subtree is None:
            query='''SELECT e.*,p.name AS place_name FROM frigate_object_events e
                     JOIN places p ON e.place_id=p.id ORDER BY e.started_at DESC LIMIT ?'''
            params=(limit,)
        else:
            marks=','.join('?' for _ in subtree)
            query=f'''SELECT e.*,p.name AS place_name FROM frigate_object_events e
                      JOIN places p ON e.place_id=p.id WHERE p.region_id IN ({marks})
                      ORDER BY e.started_at DESC LIMIT ?'''
            params=(*sorted(subtree),limit)
        return [dict(row) for row in c.execute(query,params)]


@app.get('/api/frigate/summary')
def frigate_summary(request:Request):
    with db() as c:
        rows = c.execute('''SELECT p.id AS place_id,p.name AS place_name,
            COUNT(e.event_id) AS observed_person_objects,
            SUM(CASE WHEN e.event_id IS NOT NULL AND e.ended_at IS NULL THEN 1 ELSE 0 END) AS still_active
            FROM places p LEFT JOIN frigate_object_events e ON e.place_id=p.id
            GROUP BY p.id,p.name ORDER BY p.id''').fetchall()
        allowed={p['id'] for p in admin.place_rows(c,access.effective_region(c,request,None))}
        return {'places':[dict(r) for r in rows if r['place_id'] in allowed],
                'note':'person目标事件数不等于身份去重人数或真实到访人次。'}


@app.get('/api/frigate/quality')
def frigate_quality():
    """Honest evidence summary, not a fabricated recognition accuracy metric."""
    with db() as c:
        events = [dict(r) for r in c.execute(
            "SELECT event_id,score,started_at FROM frigate_object_events "
            "ORDER BY started_at DESC LIMIT 500")]
    confidences = [float(x['score']) for x in events if x['score'] is not None]
    return {
        'task': 'anonymous_person_object_detection',
        'identity_recognition_verified': False,
        'precision': None,
        'recall': None,
        'unique_people': None,
        'stored_object_events': len(events),
        'scored_events': len(confidences),
        'mean_model_score': round(sum(confidences) / len(confidences), 4) if confidences else None,
        'score_is_calibrated_accuracy': False,
        'description': '置信分数不是准确率；计算查准率/召回率需要带人工标注的逐帧真值。' }


# ---------------------------------------------------------------------------
# Deterministic synthetic business workflow: deliberately separated from
# real video/F​​rigate events and from biometric identities.
# ---------------------------------------------------------------------------
from typing import Optional
from hashlib import sha256


def _hit_signature(hit, cfg):
    payload = {'type':hit['type'], 'key':hit['key'], 'evidence':hit['observation_ids'],
               'people':hit['people'], 'places':hit['place_ids'], 'config':cfg}
    return sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class SyntheticConfig(BaseModel):
    visit_gap_minutes: int = Field(default=20, ge=1, le=1440)
    encounter_window_minutes: int = Field(default=10, ge=1, le=120)
    companion_min_episodes: int = Field(default=2, ge=1, le=20)
    gathering_min_people: int = Field(default=3, ge=2, le=100)
    gathering_min_episodes: int = Field(default=1, ge=1, le=20)
    interaction_min_rounds: int = Field(default=1, ge=1, le=20)
    absence_days: int = Field(default=3, ge=1, le=365)
    absence_mode: Literal['days', 'missed_checks'] = 'days'
    absence_check_interval_days: int = Field(default=1, ge=1, le=365)
    absence_missed_checks: int = Field(default=3, ge=1, le=365)
    headcount_difference: int = Field(default=2, ge=1, le=1000)
    activity_start_hour: int = Field(default=8, ge=0, le=22)
    activity_end_hour: int = Field(default=18, ge=1, le=23)
    activity_timezone_offset_hours: int = Field(default=0, ge=-12, le=14)
    enabled_rules: list[Literal['cross_place', 'companion', 'interaction', 'gathering',
                                'absence', 'appearance', 'headcount']] = Field(
        default_factory=lambda: list(RULE_NAMES))
    watch_place_id: int = Field(default=0, ge=0)
    affiliation_mode: Literal['earliest', 'frequency'] = 'earliest'


class SyntheticObservationReview(BaseModel):
    excluded: bool = False
    corrected_person: Optional[str] = Field(default=None, pattern=r'^P[0-9]{3}$')
    reason: str = Field(min_length=2, max_length=300)


class SyntheticAlertReview(BaseModel):
    decision: Literal['confirmed', 'false_positive', 'needs_review']
    reason: str = Field(min_length=2, max_length=300)


def _synthetic_config(c):
    row = c.execute('SELECT json_value FROM synthetic_settings WHERE id=1').fetchone()
    return {**DEFAULT_CONFIG, **(json.loads(row['json_value']) if row else {})}


def _effective_synthetic_observations(c):
    return [dict(r) for r in c.execute('''
        SELECT o.*,p.name AS place_name,
        (SELECT r.excluded FROM synthetic_observation_reviews r
         WHERE r.observation_id=o.id ORDER BY r.id DESC LIMIT 1) AS excluded,
        (SELECT r.corrected_person FROM synthetic_observation_reviews r
         WHERE r.observation_id=o.id ORDER BY r.id DESC LIMIT 1) AS effective_person
        FROM synthetic_observations o JOIN places p ON p.id=o.place_id
        ORDER BY o.observed_at,o.id
    ''')]


def _analyze_synthetic(c):
    cfg = _synthetic_config(c)
    observations = _effective_synthetic_observations(c)
    # Synthetic scenarios have their own clock. The fixture intentionally
    # contains events throughout a demonstration day; using real wall-clock
    # before those times would analyze FUTURE events as if already observed.
    if observations:
        latest = max(datetime.fromisoformat(o['observed_at'].replace('Z','+00:00'))
                     for o in observations)
        scenario_now = latest.astimezone(timezone.utc) + timedelta(minutes=30)
    else:
        scenario_now = datetime.now(timezone.utc)
    home, roster_status = base_registry.active_assignments(c)
    result = analyze(observations, cfg, now=scenario_now, affiliation_overrides=home)
    result['registry_status'] = roster_status
    result['simulation_time_utc'] = scenario_now.isoformat(timespec='seconds')
    statuses = {r['alert_key']:dict(r) for r in c.execute('''
      SELECT r.alert_key,r.decision,r.evidence_signature FROM synthetic_alert_reviews r
      INNER JOIN (SELECT alert_key,MAX(id) AS latest_id FROM synthetic_alert_reviews
                  GROUP BY alert_key) last ON last.latest_id=r.id
    ''')}
    for hit in result['alerts']:
        signature = _hit_signature(hit,{**cfg,'registry':roster_status})
        hit['review_signature'] = signature
        previous = statuses.get(hit['key'])
        hit['review_stale'] = bool(previous and previous['evidence_signature'] != signature)
        hit['status'] = (previous['decision'] if previous and not hit['review_stale'] else 'needs_review')
    result['config'] = cfg
    return result


@app.get('/api/business/config')
def get_synthetic_config():
    with db() as c:
        return _synthetic_config(c)


@app.put('/api/business/config')
def put_synthetic_config(data: SyntheticConfig):
    with db() as c:
        cfg = data.model_dump()
        if cfg['activity_start_hour'] >= cfg['activity_end_hour']:
            raise HTTPException(422, '活动结束时间必须晚于开始时间；目前仅支持同日时段')
        if len(cfg['enabled_rules']) != len(set(cfg['enabled_rules'])):
            raise HTTPException(422, '规则列表不得重复')
        if cfg['watch_place_id'] and not c.execute('SELECT 1 FROM places WHERE id=?',(cfg['watch_place_id'],)).fetchone():
            raise HTTPException(404, '指定关注场所不存在')
        c.execute('INSERT INTO synthetic_settings(id,json_value) VALUES(1,?) '
                  'ON CONFLICT(id) DO UPDATE SET json_value=excluded.json_value',
                  (json.dumps(cfg,ensure_ascii=False),))
        c.execute('INSERT INTO synthetic_audit(operation,detail,created_at) VALUES(?,?,?)',
                  ('config_changed',json.dumps(cfg,ensure_ascii=False),utcnow()))
        result = _analyze_synthetic(c)
    return {'saved':cfg,'recomputed':True,'visit_count':result['visit_count'],
            'alert_count':len(result['alerts']),'note':'未改变原始合成事件或Frigate真实观测。'}


@app.post('/api/business/seed')
def seed_synthetic_business(reset: bool = False):
    # Create explicitly fake observation data. No external camera input.
    with db() as c:
        count = c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]
        if count and not reset:
            return {'inserted':0, 'existing':count,'idempotent':True,
                    'note':'已存在演示数据；使用 reset=true 才会清空并重建合成数据。'}
        if reset:
            # A synthetic scenario reset invalidates any previously published roster.
            c.execute("UPDATE synthetic_registry_runs SET state='archived' WHERE state!='archived'")
            c.execute('DELETE FROM synthetic_observation_reviews')
            c.execute('DELETE FROM synthetic_alert_reviews')
            c.execute('DELETE FROM synthetic_observations')
        names = ('测试场所 A','测试场所 B','测试场所 C')
        place_ids = []
        for name in names:
            c.execute('INSERT OR IGNORE INTO places(name,region_id) VALUES(?,?)',(name,admin.demo_leaf(c)))
            c.execute('UPDATE places SET region_id=? WHERE name=? AND region_id IS NULL',
                      (admin.demo_leaf(c),name))
            place_ids.append(c.execute('SELECT id FROM places WHERE name=?',(name,)).fetchone()[0])
        A,B,C = place_ids
        # Today (UTC), synthetic observation moments. P006 only existed 4 days ago.
        base = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        fixtures = [
            ('P006',A,-4,9,0),
            ('P001',A,0,9,0),('P001',A,0,9,3),('P002',A,0,9,2),
            ('P004',C,0,11,0),('P005',C,0,11,1),
            ('P001',C,0,11,2),('P002',C,0,11,3),
            ('P003',B,0,10,0),
            ('P001',B,0,12,0),('P002',B,0,12,3),('P003',B,0,12,5),
            ('P003',A,0,16,0),('P001',A,0,16,3),
            ('P001',B,0,18,0),('P002',B,0,18,2),
        ]
        for idx,(person,place,day,hour,minute) in enumerate(fixtures,1):
            ts=(base+timedelta(days=day,hours=hour,minutes=minute)).isoformat()
            c.execute('''INSERT INTO synthetic_observations
                (event_key,person_label,place_id,observed_at,origin)
                VALUES (?,?,?,?,?)''',(f'DEMO-{idx:04d}',person,place,ts,'synthetic_only'))
        cfg = {**DEFAULT_CONFIG,'watch_place_id':B}
        c.execute('INSERT INTO synthetic_settings(id,json_value) VALUES(1,?) '
                  'ON CONFLICT(id) DO UPDATE SET json_value=excluded.json_value',
                  (json.dumps(cfg),))
        c.execute('INSERT INTO synthetic_audit(operation,detail,created_at) VALUES(?,?,?)',
                  ('reset_seed' if reset else 'seed',f'{len(fixtures)} synthetic observations',utcnow()))
        result = _analyze_synthetic(c)
        return {'inserted':len(fixtures),'existing':0,'mode':'synthetic_only',
                'visits':result['visit_count'],'rule_counts':result['rule_counts'],
                'note':'所有P001等身份及其时间、场所均为虚构；不来自视频或人脸识别。'}


@app.get('/api/business/observations')
def synthetic_observations():
    with db() as c:
        return _effective_synthetic_observations(c)


@app.post('/api/business/observations/{observation_id}/review')
def synthetic_observation_review(observation_id: int, data: SyntheticObservationReview):
    with db() as c:
        if not c.execute('SELECT 1 FROM synthetic_observations WHERE id=?',(observation_id,)).fetchone():
            raise HTTPException(404, '演示观测不存在')
        c.execute('''INSERT INTO synthetic_observation_reviews
            (observation_id,excluded,corrected_person,reason,reviewed_at) VALUES(?,?,?,?,?)''',
            (observation_id,int(data.excluded),data.corrected_person,data.reason,utcnow()))
        c.execute('INSERT INTO synthetic_audit(operation,detail,created_at) VALUES(?,?,?)',
                  ('observation_review',f'observation_id={observation_id}; {data.model_dump_json()}',utcnow()))
        r = _analyze_synthetic(c)
        return {'reviewed':observation_id,'original_unchanged':True,
                'visit_count':r['visit_count'],'alert_count':len(r['alerts'])}


@app.get('/api/business/analysis')
def synthetic_analysis():
    with db() as c:
        return _analyze_synthetic(c)


@app.post('/api/business/alerts/{alert_key}/review')
def synthetic_alert_review(alert_key: str, data: SyntheticAlertReview):
    with db() as c:
        current = {a['key']:a for a in _analyze_synthetic(c)['alerts']}
        if alert_key not in current:
            raise HTTPException(404,'此规则命中已不存在；请先刷新分析结果')
        c.execute('''INSERT INTO synthetic_alert_reviews
            (alert_key,decision,reason,reviewed_at,evidence_signature) VALUES(?,?,?,?,?)''',
            (alert_key,data.decision,data.reason,utcnow(),current[alert_key]['review_signature']))
        c.execute('INSERT INTO synthetic_audit(operation,detail,created_at) VALUES(?,?,?)',
                  ('alert_review',f'key={alert_key}; {data.model_dump_json()}',utcnow()))
        return {'alert_key':alert_key,'decision':data.decision,
                'note':'审核是独立结论，不删除原始模拟证据，不更改规则命中。'}


@app.get('/api/business/audit')
def synthetic_audit():
    with db() as c:
        return [dict(row) for row in c.execute('SELECT * FROM synthetic_audit ORDER BY id DESC LIMIT 100')]


@app.get('/api/frigate/status')
def frigate_status():
    """Probe the configured upstream; does not infer video inference from reachability."""
    base = os.getenv('ZD_FRIGATE_API', 'http://127.0.0.1:5000').rstrip('/')
    try:
        response = httpx.get(base + '/api/version', timeout=3.0)
        response.raise_for_status()
        return {'connected':True,'upstream_version':response.text.strip('\"\n '),
                'inference_verified':False,
                'note':'HTTP已连接；必须用带person的授权测试视频验证目标检测和事件产生。'}
    except httpx.HTTPError as e:
        return {'connected':False, 'upstream_version':None,
                'inference_verified':False,
                'note':f'Frigate无响应：{type(e).__name__}；查看容器状态和5000端口绑定。'}


# Synthetic-only operational dashboard + people/visit search. Never join a
# Frigate object event to a synthetic identity. Real metrics remain separate.
@app.get('/api/insights/overview')
def get_insights_overview(request:Request, days: int = Query(7, ge=1, le=31),
                          region_id: int | None = Query(None, ge=1),
                          place_id: int | None = Query(None, ge=1)):
    with db() as c:
        region_id=access.effective_region(c,request,region_id)
        allowed = admin.subtree_ids(c, region_id)
        rows = admin.place_rows(c,region_id)
        if place_id is not None:
            if place_id not in {p['id'] for p in rows}:
                raise HTTPException(404, '场所不在当前查询范围')
            rows = [p for p in rows if p['id']==place_id]
        synth = insights.overview(_analyze_synthetic(c),rows,days=days)
        # Don't silently mix source-based event counts and fictitious people.
        video = monitoring_overview(c,days=days,region_id=region_id,
                                    allowed_region_ids=allowed)
        if place_id is not None:
            video['places']=[v for v in video['places'] if v['place_id']==place_id]
            video['counts']={k:sum(v[k] for v in video['places'])
                             for k in ('person_objects','sampled_frames','face_detections')}
            video['timeline']=[]  # avoid exposing unscoped aggregate day data
        return {'synthetic':synth,'real_video':video,
                'note':'真实视频聚合与合成人员统计严格隔离；不做身份对应。'}


@app.get('/api/insights/people')
def search_insights_people(request:Request, q: str = Query('',max_length=40),
                           days: int = Query(30,ge=1,le=365),
                           place_id: int | None = Query(None,ge=1),
                           region_id: int | None = Query(None,ge=1)):
    with db() as c:
        region_id=access.effective_region(c,request,region_id)
        return insights.people_search(_analyze_synthetic(c), admin.place_rows(c,region_id),
                                      days=days,query=q,place_id=place_id)


@app.get('/api/insights/people/{person}')
def get_insights_person(request:Request, person: str, days: int = Query(30,ge=1,le=365),
                        region_id: int | None = Query(None,ge=1)):
    if not __import__('re').fullmatch(r'P[0-9]{3}',person):
        raise HTTPException(422, '只能查询虚构的 P### 人员编号')
    with db() as c:
        region_id=access.effective_region(c,request,region_id)
        return insights.person_detail(_analyze_synthetic(c),admin.place_rows(c,region_id),
                                      person,days=days)


@app.get('/api/insights/relationships')
def get_insights_relationships(request:Request, days: int = Query(30,ge=1,le=365),
                               region_id: int | None = Query(None,ge=1),
                               place_id: int | None = Query(None,ge=1),
                               person: str | None = Query(None,pattern=r'^P[0-9]{3}$')):
    with db() as c:
        region_id=access.effective_region(c,request,region_id)
        rows=admin.place_rows(c,region_id)
        place_ids={p['id'] for p in rows}
        if place_id is not None:
            if place_id not in place_ids: raise HTTPException(404,'场所不在查询范围')
            place_ids={place_id}
        return insights.relationships(_analyze_synthetic(c),place_ids=place_ids,
                                      days=days,person=person)
