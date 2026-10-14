"""Minimal, local-only real video ingestion -> detection -> durable events -> reviews -> stats.

Identity recognition and cross-person tracking are deliberately out of scope here.
Demo records are always clearly labeled as simulated.
"""
import os
import sys
import json
import sqlite3
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal
from contextlib import asynccontextmanager

import cv2
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response, JSONResponse, RedirectResponse
from app.detector import choose_detector, detector_info, box_quality
from app.media_probe import probe_video
from app.frigate_bridge import parse_person_event
from app.business import analyze, DEFAULT_CONFIG, RULE_NAMES
from app.monitoring import overview as monitoring_overview, face_activity_details
from app import admin, base_registry, insights, access, gb28181, continuous_capture, rule_instances, anime_scenes, anime_ontology, cctv_dataset, site_model, occurrences, occurrence_reviews, research_linkage, epfl_review, geo_demo, face_photo_lab, real_evidence, real_activity, demo_excel_alerts, real_scene_aggregate
import httpx
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv('ZD_DATA_DIR', str(ROOT / 'data' / 'real-only')))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / 'demo.sqlite3'
STATIC = ROOT / 'static' / 'index.html'
PORTAL_STATIC = ROOT / 'static' / 'portal.html'
BUSINESS_STATIC = ROOT / 'static' / 'business.html'
MONITOR_STATIC = ROOT / 'static' / 'monitor.html'
ADMIN_STATIC = ROOT / 'static' / 'admin.html'
INSIGHTS_STATIC = ROOT / 'static' / 'insights.html'
LOGIN_STATIC = ROOT / 'static' / 'login.html'


def _file_revision(path: str) -> str:
    """Content identity of an authorized local source file, not a person ID.

    Replaying the same file must not inflate monitoring counts. Using a content
    digest rather than mtime/size also handles same-size source replacement.
    """
    digest = hashlib.sha256()
    with open(path, 'rb') as footage:
        for chunk in iter(lambda: footage.read(1024 * 1024), b''):
            digest.update(chunk)
    return 'sha256:' + digest.hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def legacy_synthetic_tests_enabled() -> bool:
    """Legacy synthetic regression fixtures are never available in a normal server.

    The archived P### code stays testable under pytest, but launching the
    application, browser, or local demo always defaults to REAL-ONLY.
    """
    return (os.getenv("ZD_LEGACY_MODE") == "1"
            and bool(os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules))


def real_only_blocked(path: str) -> bool:
    """Fail closed for legacy fake-person UI, seeds, and business APIs."""
    page_roots = ("/base", "/business", "/insights", "/geo", "/anime-scenes")
    api_roots = ("/api/base", "/api/business", "/api/insights",
                 "/api/geo", "/api/anime", "/api/demo",
                 "/api/rules", "/api/events", "/api/reviews", "/api/summary")
    return any(path == p or path.startswith(p + "/")
               for p in (*page_roots, *api_roots))


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
            detector TEXT NOT NULL DEFAULT 'legacy',
            source_revision TEXT
        );
        CREATE INDEX IF NOT EXISTS samples_place ON samples(place_id, id);
        CREATE TABLE IF NOT EXISTS face_boxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
            detector_score REAL,
            x INTEGER NOT NULL, y INTEGER NOT NULL, w INTEGER NOT NULL, h INTEGER NOT NULL,
            frame_width INTEGER NOT NULL, frame_height INTEGER NOT NULL,
            quality_state TEXT NOT NULL CHECK(quality_state IN ('candidate','needs_review')),
            quality_reasons TEXT NOT NULL,
            sharpness REAL NOT NULL,
            quality_assessment TEXT NOT NULL DEFAULT 'uncalibrated_heuristic'
        );
        CREATE INDEX IF NOT EXISTS face_boxes_by_sample ON face_boxes(sample_id);
        CREATE TABLE IF NOT EXISTS face_box_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            box_id INTEGER NOT NULL REFERENCES face_boxes(id) ON DELETE CASCADE,
            decision TEXT NOT NULL CHECK(decision IN ('false_positive','plausible','unreviewed')),
            reason TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            reviewed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS face_box_reviews_latest ON face_box_reviews(box_id,id);
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
            event_id TEXT NOT NULL,
            camera TEXT NOT NULL REFERENCES frigate_camera_bindings(camera),
            place_id INTEGER NOT NULL REFERENCES places(id),
            label TEXT NOT NULL CHECK(label='person'),
            started_at TEXT NOT NULL,
            ended_at TEXT,
            score REAL,
            has_snapshot INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(camera,event_id)
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
        # Legacy rows keep a NULL digest because the original video content
        # cannot be proven after the fact. New file observations have an
        # immutable source digest and can be re-imported idempotently.
        if 'source_revision' not in cols:
            c.execute('ALTER TABLE samples ADD COLUMN source_revision TEXT')
        c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS sample_file_frame_revision
          ON samples(source_id,source_revision,frame_index,detector)
          WHERE origin='video' AND source_revision IS NOT NULL""")
        # V1 observation event semantics: old rows keep NULL, meaning each
        # individual event_key was one explicit appearance event.
        # Compatibility migration: older schema used event_id as a *global*
        # primary key. A track ID is scoped to its actual upstream camera;
        # similarly named events in distinct cameras must not overwrite.
        event_cols = c.execute('PRAGMA table_info(frigate_object_events)').fetchall()
        if any(r['name']=='event_id' and r['pk']==1 for r in event_cols) and not any(
                r['name']=='camera' and r['pk']>0 for r in event_cols):
            c.execute('ALTER TABLE frigate_object_events RENAME TO frigate_object_events_legacy')
            c.execute('''CREATE TABLE frigate_object_events (
                event_id TEXT NOT NULL,
                camera TEXT NOT NULL REFERENCES frigate_camera_bindings(camera),
                place_id INTEGER NOT NULL REFERENCES places(id),
                label TEXT NOT NULL CHECK(label='person'),
                started_at TEXT NOT NULL,
                ended_at TEXT,
                score REAL,
                has_snapshot INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(camera,event_id))''')
            c.execute('''INSERT INTO frigate_object_events
                (event_id,camera,place_id,label,started_at,ended_at,score,has_snapshot,updated_at)
                SELECT event_id,camera,place_id,label,started_at,ended_at,score,has_snapshot,updated_at
                FROM frigate_object_events_legacy''')
            c.execute('DROP TABLE frigate_object_events_legacy')
            c.execute('CREATE INDEX IF NOT EXISTS frigate_place_date ON frigate_object_events(place_id,started_at)')
        observation_cols = [r['name'] for r in c.execute('PRAGMA table_info(synthetic_observations)')]
        if 'occurrence_key' not in observation_cols:
            c.execute('ALTER TABLE synthetic_observations ADD COLUMN occurrence_key TEXT')
        review_cols = [r['name'] for r in c.execute('PRAGMA table_info(synthetic_alert_reviews)')]
        if 'evidence_signature' not in review_cols:
            c.execute('ALTER TABLE synthetic_alert_reviews ADD COLUMN evidence_signature TEXT')
        # No fake places or canned media in a real-only database.
        # Existing historical fixtures remain intact, but are never loaded
        # into live analytics, and can no longer be generated by the API.
        if legacy_synthetic_tests_enabled():
            c.execute("INSERT OR IGNORE INTO places (name) VALUES ('测试场所 A')")
            c.execute("INSERT OR IGNORE INTO places (name) VALUES ('测试场所 B')")
            bundled_video = ROOT / 'samples' / 'test-pattern.mp4'
            if bundled_video.is_file():
                place_id = c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
                if not c.execute("SELECT 1 FROM sources WHERE name='内置测试视频（无真人）'").fetchone():
                    c.execute('INSERT INTO sources(place_id,name,kind,uri) VALUES(?,?,?,?)',
                              (place_id, '内置测试视频（无真人）', 'file', str(bundled_video)))
        admin.initialize(c)
        geo_demo.initialize(c)
        base_registry.initialize(c)
        rule_instances.initialize(c)
        access.initialize(c)
        gb28181.initialize(c)
        continuous_capture.initialize(c)
        anime_scenes.initialize(c)
        anime_ontology.initialize(c)
        cctv_dataset.initialize(c)
        site_model.initialize(c)
        occurrence_reviews.initialize(c)
        research_linkage.initialize(c)
        epfl_review.initialize(c)
        if 'binding_epoch_id' not in {r['name'] for r in c.execute(
                'PRAGMA table_info(frigate_object_events)')}:
            c.execute('ALTER TABLE frigate_object_events ADD COLUMN binding_epoch_id INTEGER')


setup_db()


@asynccontextmanager
async def app_lifespan(application):
    """Start durable collection only when an ASGI server enters lifespan."""
    continuous_capture.start_pending()
    try:
        yield
    finally:
        continuous_capture.shutdown()


app = FastAPI(title='ZD Real Video Evidence', version='1.1.0', lifespan=app_lifespan)


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error(request: Request, error: RequestValidationError):
    """Never JSON-serialize malformed raw inputs such as 1e999/Infinity.

    FastAPI validation messages can include 'input': float('inf'), which cannot
    be encoded into a strict JSON response, otherwise causing a 500 instead
    of a 422. Do not echo private request bodies in public API error payloads.
    """
    details=[{'loc':[str(v) for v in e.get('loc',())],
              'msg':str(e.get('msg','Invalid input')),
              'type':str(e.get('type','value_error'))}
             for e in error.errors()]
    return JSONResponse(status_code=422,content={'detail':details})

admin.register(app,db)
base_registry.register(app,db)
access.register(app,db)
rule_instances.register(app,db)
anime_scenes.register(app,db,_file_revision,DATA_DIR / 'anime_uploads')
anime_ontology.register(app,db,_file_revision)
cctv_dataset.register(app,db,DATA_DIR / 'public_cctv')
site_model.register(app,db)
occurrences.register(app,db)
occurrence_reviews.register(app,db)
research_linkage.register(app,db)
epfl_review.register(app,db)
gb28181.register(app,db)
geo_demo.register(app,db,lambda c: _analyze_synthetic(c))
face_photo_lab.register(app)
real_evidence.register(app,db)
real_activity.register(app,db)
demo_excel_alerts.register(app)
real_scene_aggregate.register(app)


@app.middleware('http')
async def enforce_role_scope(request: Request, call_next):
    """Default-deny API gateway; region filters are additionally imposed by endpoints.

    Old regression tests may explicitly enable the compatibility mode, which is
    ONLY effective inside an active pytest test. Never used by the running server.
    """
    path = request.url.path
    if not legacy_synthetic_tests_enabled() and real_only_blocked(path):
        return JSONResponse(
            {'detail': '仅真实视频模式：合成人员、模拟事件及模拟规则已禁用'},
            status_code=410)
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


class FaceBoxReviewIn(BaseModel):
    decision: Literal['false_positive', 'plausible', 'unreviewed']
    reason: str = Field(min_length=2, max_length=300)


class RuleIn(BaseModel):
    face_frame_threshold: int = Field(ge=1, le=100)


@app.get('/')
def home():
    return FileResponse(STATIC) if legacy_synthetic_tests_enabled() else RedirectResponse('/real',status_code=307)


@app.get('/ui/role-navigation.js')
def role_navigation_script():
    return FileResponse(ROOT / 'static' / 'role_navigation.js',
                        media_type='text/javascript')

@app.get('/ui/site-navigation.js')
def operational_site_navigation_script():
    return FileResponse(ROOT / 'static' / 'site_navigation.js',
                        media_type='text/javascript',headers={"Cache-Control":"no-store"})


@app.get('/ui/showcase.js')
def operational_site_script():
    return FileResponse(ROOT / 'static' / 'showcase_ui.js',
                        media_type='text/javascript', headers={"Cache-Control":"no-store"})



@app.get('/portal')
def portal_home():
    return FileResponse(PORTAL_STATIC) if legacy_synthetic_tests_enabled() else RedirectResponse('/real',status_code=307)


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

@app.get('/anime-scenes')
def anime_scene_home():
    return FileResponse(ROOT / 'static' / 'anime-scenes.html')

@app.get('/cctv')
def cctv_home():
    return FileResponse(ROOT / 'static' / 'cctv.html')


@app.get('/api/monitoring/overview')
def get_monitoring_overview(request:Request, days: int = Query(7, ge=1, le=31), region_id: int | None = Query(None,ge=1)):
    with db() as c:
        region_id=access.effective_region(c,request,region_id)
        return monitoring_overview(c, days=days,region_id=region_id,
                      allowed_region_ids=admin.subtree_ids(c,region_id))


@app.get('/api/monitoring/face-activity')
def get_face_activity_details(request:Request, days:int=Query(7,ge=1,le=31),
                              limit:int=Query(50,ge=1,le=250),
                              region_id:int|None=Query(None,ge=1)):
    with db() as c:
        region_id=access.effective_region(c,request,region_id)
        return face_activity_details(c,days=days,limit=limit,region_id=region_id,
                   allowed_region_ids=admin.subtree_ids(c,region_id))


# One read-only source of truth for the original Excel 23-item demonstration.
# Never infer an item is fulfilled just because a route/test happens to exist.
EXCEL_DEMO_ROUTES = {
  1: '/cctv#epflResearchPanel', 2: '/admin', 3: '/base',
  4: '/insights', 5: '/insights', 6: '/business', 7: '/business',
  8: '/business', 9: '/business', 10: '/business', 11: '/business',
  12: '/business', 13: '/cctv', 14: '/insights', 15: '/insights',
  16: '/monitor', 17: '/insights', 18: '/insights', 19: '/insights',
  20: '/admin', 21: '/base', 22: '/business', 23: '/admin'
}


@app.get('/api/demo/excel-acceptance')
def excel_acceptance(request: Request):
    actor = access.principal(request)
    if not actor or actor['level'] != 1:
        raise HTTPException(403, '仅州级管理员可审阅交付验收全表')
    # Read exactly the *same* manifest as tests/test_excel_acceptance_contract.py.
    manifest = json.loads((ROOT / 'config/excel_acceptance_v1.json').read_text(
        encoding='utf-8'))
    req = manifest['requirements']
    if [x['id'] for x in req] != list(range(1,24)):
        raise HTTPException(503, 'Excel验收清单不完整')
    items = [{
        'id': x['id'],
        'title': x['title'],
        'status': x['status'],
        'verified_scope': x['verified_scope'],
        'remaining_gap': x['remaining_gap'],
        'demo_url': EXCEL_DEMO_ROUTES[x['id']],
        'evidence_files': x['evidence_files'],
        'delivery_mode': x['delivery_mode'],
        'delivered_now': x['delivered_now'],
        'alternative_used': x['alternative_used'],
        'why_demo_usable': x['why_demo_usable'],
        'not_equivalent_to': x['not_equivalent_to'],
        'future_implementation': x['future_implementation'],
        'acceptance_gate': x['acceptance_gate'],
        'next_priority': x['next_priority'],
    } for x in req]
    counts = {k:sum(x['status']==k for x in items) for k in
              ('verified_real','synthetic_only','partial','missing')}
    return {
        'source_file': manifest['source_file'],
        'source_worksheet': manifest['source_worksheet'],
        'requirements': items, 'counts': counts,
        'delivery_assessment_date': manifest['delivery_assessment_date'],
        'delivery_baseline_ci': manifest['delivery_baseline_ci'],
        'substitution_scope': manifest['substitution_scope'],
        'delivery_modes': manifest['delivery_modes'],
        'all_23_verified_real': False,
        'warning': '这是严格的原Excel需求追踪，不代表23项真实功能全部完成；P###预警仍是合成身份业务。'
    }


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
        site_model.record_initial(c,cursor.lastrowid,data.place_id)
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


def _process_source_impl(source_id: int, max_frames: int = 90,
                         sample_every: int = 10, *, worker_mode: bool = False,
                         _capture=None, _detector=None, _frame_base: int = 0,
                         _expected_source=None):
    with db() as c:
        if not worker_mode and c.execute(
                'SELECT 1 FROM capture_jobs WHERE source_id=? AND desired_enabled=1',
                (source_id,)).fetchone():
            raise HTTPException(409, '该源正在持续采集；请先停止任务后再手动分析')
        row = c.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
        if not row:
            raise HTTPException(404, '视频源不存在')
        source = dict(row)
        # A held-open RTSP decoder must never silently attribute old camera
        # frames to a newly rebound source or a different physical stream.
        if _expected_source is not None and (
                source['kind'], source['uri'], source['place_id']) != _expected_source:
            raise HTTPException(409, '视频源配置已更改，请重新打开采集连接')
        if not source['enabled']:
            raise HTTPException(409,'视频源已停用')
        admin._active_place(c,source['place_id'])
    if source['kind'] == 'file' and not Path(source['uri']).is_file():
        raise HTTPException(400, '视频文件路径不存在：需填写服务端机器上的路径')
    # Only file-backed content can be replay-deduplicated reliably. Realtime
    # streams/cameras deliberately have no content-based revision.
    revision = None
    if source['kind'] == 'file':
        try:
            revision = _file_revision(source['uri'])
        except OSError as exc:
            raise HTTPException(400, '无法读取视频文件以验证来源完整性') from exc
    capture_arg = int(source['uri']) if source['kind'] == 'camera' else source['uri']
    owns_capture = _capture is None
    cap = cv2.VideoCapture(capture_arg) if owns_capture else _capture
    if not cap.isOpened():
        if owns_capture:
            cap.release()
        raise HTTPException(400, '视频无法打开：检查摄像头权限、视频文件路径或RTSP流状态')
    try:
        detector = _detector if _detector is not None else choose_detector()
    except Exception as err:
        if owns_capture:
            cap.release()
        raise HTTPException(500, f'检测器初始化失败：{err}') from err
    read_count, sampled, faces_total = 0, 0, 0
    detections = []  # each item: sampled video fact, and real geometric boxes
    try:
        while read_count < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            read_count += 1
            if (read_count - 1) % sample_every:
                continue
            # Intentionally genuine CPU inference; no caching, no pre-filled results.
            boxes = detector.detect(frame)
            annotated = [(box,box_quality(frame,box)) for box in boxes]
            count = len(boxes)
            sampled += 1
            faces_total += count
            video_offset_ms = round(cap.get(cv2.CAP_PROP_POS_MSEC)) if source['kind'] == 'file' else None
            detections.append(((source_id, source['place_id'], 'video', utcnow(),
                               video_offset_ms, _frame_base + read_count, count, detector.name),
                               frame.shape[1],frame.shape[0],annotated))
    finally:
        if owns_capture:
            cap.release()
    if not read_count:
        raise HTTPException(400, '视频已打开，但未读到帧')
    if revision is not None:
        # A changed file would otherwise attach boxes from the old content
        # to a new digest, producing false provenance.
        try:
            if _file_revision(source['uri']) != revision:
                raise HTTPException(409, '视频文件在分析过程中被修改；未保存这批观测')
        except OSError as exc:
            raise HTTPException(409, '分析完成后源文件不可读；未保存这批观测') from exc
    new_samples, previously_imported, new_boxes = 0, 0, 0
    with db() as c:
        # Check the configuration INSIDE the final SQLite write transaction:
        # the operator may rebind or disable the camera while decoding a long
        # file/RTSP batch. Without this guard old frames could be attributed to
        # a now-different physical source. The write lock prevents a rebind
        # racing between the check and the persisted evidence.
        c.execute('BEGIN IMMEDIATE')
        current=c.execute('SELECT kind,uri,place_id,enabled FROM sources WHERE id=?',
                          (source_id,)).fetchone()
        if (current is None or not current['enabled'] or
            (current['kind'],current['uri'],current['place_id']) !=
            (source['kind'],source['uri'],source['place_id'])):
            raise HTTPException(409,'视频源在解码期间已重绑或停用；本批观测未入库')
        for sample,frame_width,frame_height,boxes in detections:
            cur=c.execute('''INSERT OR IGNORE INTO samples
                   (source_id,place_id,origin,ingested_at,video_offset_ms,frame_index,
                    detected_faces,detector,source_revision)
                   VALUES(?,?,?,?,?,?,?,?,?)''',(*sample, revision))
            if not cur.rowcount:
                previously_imported += 1
                continue
            new_samples += 1
            for box,quality in boxes:
                c.execute('''INSERT INTO face_boxes(sample_id,detector_score,x,y,w,h,
                  frame_width,frame_height,quality_state,quality_reasons,sharpness)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (cur.lastrowid,box['detector_score'],box['x'],box['y'],box['w'],box['h'],
                   frame_width,frame_height,quality['quality_state'],
                   json.dumps(quality['quality_reasons']),quality['sharpness_laplacian_var']))
                new_boxes += 1
    return {'source_id': source_id, 'frames_decoded': read_count,
            'frames_analyzed': sampled, 'face_detections': faces_total, 'detector': detector.name,
            'source_revision': revision, 'new_samples': new_samples,
            'previously_imported': previously_imported,
            'structured_boxes_saved': new_boxes,
            'quality_assessment': 'uncalibrated_heuristic',
            'note': '文件按内容哈希+帧号+检测器幂等导入；流数据不保证重放去重。检测数为本次推理的框数，新保存框数仅计新记录。'}



@app.post('/api/sources/{source_id}/process')
def process_source(source_id: int, max_frames: int = Query(90, ge=1, le=600),
                   sample_every: int = Query(10, ge=1, le=60)):
    """Public route cannot set internal worker mode to bypass singleton safety."""
    return _process_source_impl(source_id, max_frames, sample_every)


@app.get('/api/video/observations/{sample_id}/preview')
def preview_video_sample(sample_id:int,request:Request):
    """Generate a temporary privacy-redacted geometric overlay on SOURCE frame.

    Authenticated location scope; only an explicitly registered local video
    file may be decoded. Never stores/serves an unredacted face image.
    """
    with db() as c:
        row=c.execute('''SELECT s.*,v.kind,v.uri
          FROM samples s JOIN sources v ON v.id=s.source_id
          WHERE s.id=? AND s.origin='video' ''',(sample_id,)).fetchone()
        if not row:
            raise HTTPException(404,'该真实视频帧不存在')
        sample=dict(row)
        access.check_place(c,request,sample['place_id'])
        boxes=[dict(x) for x in c.execute('''SELECT x,y,w,h FROM face_boxes
                              WHERE sample_id=? ORDER BY id''',(sample_id,))]
    if sample['kind']!='file':
        raise HTTPException(409,'直播来源没有可离线定位的文件帧；不能伪造抓拍')
    if not Path(sample['uri']).is_file():
        raise HTTPException(409,'原始视频文件已不可用；请检查原始来源')
    # Fail closed: replacing a source file at the same path would make the
    # historical face boxes point to a DIFFERENT frame and invent evidence.
    if not sample['source_revision']:
        raise HTTPException(409,'历史观测未记录原视频摘要，无法验证画面一致性')
    try:
        if _file_revision(sample['uri']) != sample['source_revision']:
            raise HTTPException(409,'源视频内容已变化，不能用新视频冒充原始观测画面')
    except OSError as exc:
        raise HTTPException(409,'无法读取源视频验证观测来源') from exc
    cap=cv2.VideoCapture(sample['uri'])
    try:
        if not cap.isOpened():
            raise HTTPException(409,'原始视频无法打开')
        cap.set(cv2.CAP_PROP_POS_FRAMES,max(0,int(sample['frame_index'])-1))
        ok,frame=cap.read()
    finally:
        cap.release()
    if not ok:
        raise HTTPException(409,'源视频帧定位失败')
    height,width=frame.shape[:2]
    # Full-scene pixelation, and completely covered face regions. This is
    # geometric evidence only, never an original/identifying face image.
    reduced=cv2.resize(frame,(max(1,width//28),max(1,height//28)),
                       interpolation=cv2.INTER_AREA)
    redacted=cv2.resize(reduced,(width,height),interpolation=cv2.INTER_NEAREST)
    for box in boxes:
        x,y,w,h=(int(box[field]) for field in ('x','y','w','h'))
        x=max(0,x);y=max(0,y)
        right=min(width,x+w);bottom=min(height,y+h)
        cv2.rectangle(redacted,(x,y),(right,bottom),(25,25,25),-1)
        cv2.rectangle(redacted,(x,y),(right,bottom),(80,230,85),3)
    cv2.putText(redacted,'REDACTED FACE GEOMETRY - NOT IDENTITY',
                (8,min(height-8,28)),cv2.FONT_HERSHEY_SIMPLEX,0.56,
                (245,245,245),2,cv2.LINE_AA)
    ok,jpg=cv2.imencode('.jpg',redacted,[cv2.IMWRITE_JPEG_QUALITY,82])
    if not ok:
        raise HTTPException(500,'绘制脱敏检测示意图失败')
    return Response(content=jpg.tobytes(),media_type='image/jpeg',
                    headers={'Cache-Control':'no-store, private',
                             'X-Content-Type-Options':'nosniff'})


@app.post('/api/video/observations/{sample_id}/boxes/{box_id}/review')
def review_real_face_box(sample_id:int,box_id:int,data:FaceBoxReviewIn,request:Request):
    """Append-only judgement on one anonymous geometric detection, not identity."""
    with db() as c:
        found=c.execute('''SELECT s.place_id FROM face_boxes b
            JOIN samples s ON s.id=b.sample_id
            WHERE b.id=? AND s.id=? AND s.origin='video' ''',(box_id,sample_id)).fetchone()
        if not found:
            raise HTTPException(404,'检测框不存在或不属于该视频帧')
        access.check_place(c,request,found['place_id'])
        actor=access.principal(request)
        reviewer=actor['username'] if actor else 'legacy-test-only'
        c.execute('''INSERT INTO face_box_reviews(box_id,decision,reason,reviewer,reviewed_at)
                     VALUES(?,?,?,?,?)''',(box_id,data.decision,data.reason,reviewer,utcnow()))
        n=c.execute('SELECT COUNT(*) FROM face_box_reviews WHERE box_id=?',(box_id,)).fetchone()[0]
        return {'sample_id':sample_id,'box_id':box_id,'decision':data.decision,
                'reviewer':reviewer,'history_count':n,
                'raw_detection_preserved':True,
                'note':'复核仅修改人工判断状态；原始人脸框和匿名事实不变。'}


def _utc_query_timestamp(value:str|None,parameter:str):
    if value is None:
        return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:
        raise HTTPException(422,f'{parameter} 必须是带时区的ISO8601时间') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(422,f'{parameter} 必须明确包含UTC时区偏移')
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


@app.get('/api/video/observations')
def video_observations(request:Request, limit:int=Query(60,ge=1,le=250),
                       source_id:int|None=Query(None,ge=1),
                       region_id:int|None=Query(None,ge=1),
                       place_id:int|None=Query(None,ge=1),
                       offset:int=Query(0,ge=0),
                       from_utc:str|None=Query(None),
                       until_utc:str|None=Query(None),
                       only_with_boxes:bool=False,
                       review_decision:Literal['false_positive','plausible','unreviewed']|None=None):
    """Authenticated, jurisdiction-filtered provenance with real face rectangles.

    No images/embeddings/person identities are returned; quality labels are
    uncalibrated blur/size heuristics, not actual recognition confidence.
    """
    start=_utc_query_timestamp(from_utc,'from_utc')
    end=_utc_query_timestamp(until_utc,'until_utc')
    if start is not None and end is not None and start>=end:
        raise HTTPException(422,'from_utc 必须早于 until_utc')
    with db() as c:
        scope=access.effective_region(c,request,region_id)
        allowed={p['id'] for p in admin.place_rows(c,scope)}
        if place_id is not None:
            access.check_place(c,request,place_id)
            if place_id not in allowed:
                raise HTTPException(403,'场所不在所选辖区范围内')
        if source_id is not None:
            src=c.execute('SELECT place_id FROM sources WHERE id=?',(source_id,)).fetchone()
            if not src or src['place_id'] not in allowed:
                raise HTTPException(403,'视频源不在当前辖区')
        if not allowed:
            return {'mode':'real_video_anonymous','observations':[],
                    'matched_total':0,'offset':offset,'next_offset':None,
                    'filters':{'place_id':place_id,'source_id':source_id,
                               'from_utc':start,'until_utc':end,
                               'only_with_boxes':only_with_boxes,
                               'review_decision':review_decision},
                    'quality_total_scope':'authorized_sources_all_time_unfiltered',
                    'counts':{'sampled_frames':0,'face_boxes':0},
                    'quality_totals':{'sampled_frame_records':0,'raw_face_detections':0,
                                      'no_face_frames':0,'boxes_with_geometry':0,
                                      'candidate_boxes':0,'needs_review_boxes':0,
                                      'quality_assessment':'uncalibrated_heuristic',
                                      'legacy_detections_without_geometry':0,
                                      'manual_false_positive_boxes':0,
                                      'manual_plausible_boxes':0,
                                      'unreviewed_boxes':0,
                                      'not_rejected_boxes':0},
                    'bounded_by_limit':limit,'snapshot_images_stored':False}
        marks=','.join('?' for _ in allowed)
        filter_source=' AND s.source_id=?' if source_id is not None else ''
        bind=(*sorted(allowed), *((source_id,) if source_id is not None else ()))
        # ALL scoped records: quality metrics must not depend on display LIMIT.
        # Frame-level detections are anonymous; a quality "candidate" is NOT
        # a verified unique person or a usable facial identity.
        scope_sql=f"s.origin='video' AND s.place_id IN ({marks}){filter_source}"
        totals=c.execute(f'''SELECT COUNT(*) AS sampled_frame_records,
            COALESCE(SUM(s.detected_faces),0) AS raw_face_detections,
            COALESCE(SUM(CASE WHEN s.detected_faces=0 THEN 1 ELSE 0 END),0)
              AS no_face_frames
            FROM samples s WHERE {scope_sql}''',bind).fetchone()
        quality=c.execute(f'''SELECT COUNT(b.id) AS boxes_with_geometry,
            COALESCE(SUM(CASE WHEN b.quality_state='candidate' THEN 1 ELSE 0 END),0)
              AS candidate_boxes,
            COALESCE(SUM(CASE WHEN b.quality_state='needs_review' THEN 1 ELSE 0 END),0)
              AS needs_review_boxes,
            COALESCE(SUM(CASE WHEN r.decision='false_positive' THEN 1 ELSE 0 END),0)
              AS manual_false_positive_boxes,
            COALESCE(SUM(CASE WHEN r.decision='plausible' THEN 1 ELSE 0 END),0)
              AS manual_plausible_boxes,
            COALESCE(SUM(CASE WHEN r.decision IS NULL OR r.decision='unreviewed'
                              THEN 1 ELSE 0 END),0) AS unreviewed_boxes
            FROM face_boxes b JOIN samples s ON s.id=b.sample_id
            LEFT JOIN face_box_reviews r ON r.id=(
                SELECT MAX(ir.id) FROM face_box_reviews ir WHERE ir.box_id=b.id)
            WHERE {scope_sql}''',bind).fetchone()
        all_quality={**dict(totals),**dict(quality),
            'not_rejected_boxes':int(quality['boxes_with_geometry'])-int(quality['manual_false_positive_boxes']),
            'quality_assessment':'uncalibrated_heuristic',
            'legacy_detections_without_geometry':max(
                0,int(totals['raw_face_detections'])-int(quality['boxes_with_geometry']))}
        # Evidence search uses the ORIGINAL sample venue, not the source's
        # current place after a camera relocation. All filters run in SQL
        # BEFORE LIMIT/OFFSET, and their count never means unique people.
        search_sql=scope_sql
        search_args=list(bind)
        if place_id is not None:
            search_sql+=' AND s.place_id=?'
            search_args.append(place_id)
        if start is not None:
            search_sql+=' AND julianday(s.ingested_at)>=julianday(?)'
            search_args.append(start)
        if end is not None:
            search_sql+=' AND julianday(s.ingested_at)<julianday(?)'
            search_args.append(end)
        if only_with_boxes or review_decision is not None:
            predicate='''EXISTS(SELECT 1 FROM face_boxes eb
                  WHERE eb.sample_id=s.id'''
            if review_decision is not None:
                predicate+=''' AND COALESCE(
                    (SELECT r.decision FROM face_box_reviews r
                     WHERE r.box_id=eb.id ORDER BY r.id DESC LIMIT 1),
                    'unreviewed')=?'''
                search_args.append(review_decision)
            search_sql+=' AND '+predicate+')'
        total_matches=c.execute('SELECT COUNT(*) FROM samples s WHERE '+search_sql,
                                search_args).fetchone()[0]
        rows=[dict(x) for x in c.execute(f'''SELECT s.id,s.source_id,s.place_id,
            so.name AS source_name,p.name AS place_name,s.ingested_at,
            s.frame_index,s.video_offset_ms,s.detector,s.detected_faces,
            s.source_revision
            FROM samples s JOIN sources so ON s.source_id=so.id
            JOIN places p ON p.id=s.place_id
            WHERE {search_sql}
            ORDER BY s.id DESC LIMIT ? OFFSET ?''',
            (*search_args,limit,offset))]
        for row in rows:
            faces=[]
            for v in c.execute('''SELECT b.id,b.x,b.y,b.w,b.h,b.frame_width,b.frame_height,
                        b.detector_score,b.quality_state,b.quality_reasons,b.sharpness,
                        (SELECT r.decision FROM face_box_reviews r WHERE r.box_id=b.id
                           ORDER BY r.id DESC LIMIT 1) AS review_decision,
                        (SELECT r.reviewed_at FROM face_box_reviews r WHERE r.box_id=b.id
                           ORDER BY r.id DESC LIMIT 1) AS reviewed_at
                        FROM face_boxes b WHERE b.sample_id=? ORDER BY b.id''',(row['id'],)):
                b=dict(v)
                b['quality_reasons']=json.loads(b['quality_reasons'])
                b['quality_assessment']='uncalibrated_heuristic'
                faces.append(b)
            row['boxes']=faces
        return {'mode':'real_video_anonymous','observations':rows,
                'matched_total':total_matches,'offset':offset,
                'next_offset':offset+len(rows) if offset+len(rows)<total_matches else None,
                'filters':{'place_id':place_id,'source_id':source_id,
                           'from_utc':start,'until_utc':end,
                           'only_with_boxes':only_with_boxes,
                           'review_decision':review_decision},
                'quality_total_scope':'authorized_sources_all_time_unfiltered',
                # Legacy 'counts' remain PAGE counts for existing callers.
                'counts':{'sampled_frames':len(rows),
                          'face_boxes':sum(len(x['boxes']) for x in rows)},
                'quality_totals':all_quality,
                'bounded_by_limit':limit,'snapshot_images_stored':False,
                'note':'按真实入库UTC时间、历史场所、摄像机源和最新框审核状态检索；matched_total是匹配帧数，不是独立真人数；一帧匹配筛选后仍展示该帧所有检测框。quality_totals单独表示授权来源全部历史统计。没有人脸图片存储或身份推断。'}


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
        # The inventory pointer and its effective-dated historic provenance
        # are changed atomically. Never rewrite earlier event place_id.
        c.execute('BEGIN IMMEDIATE')
        epoch=site_model.set_frigate_binding(c,data.camera,data.place_id)
        c.execute('''INSERT INTO frigate_camera_bindings(camera,place_id) VALUES(?,?)
                   ON CONFLICT(camera) DO UPDATE SET place_id=excluded.place_id''',
                  (data.camera, data.place_id))
        admin.audit(c,'frigate_binding',f'camera:{data.camera}',
                   {'place_id':old['place_id']} if old else None,
                   {**data.model_dump(),'binding_epoch_id':epoch['id']})
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
    inserted, updated, skipped, unlocated = 0, 0, 0, 0
    with db() as c:
        bindings = {r['camera']: r['place_id'] for r in c.execute('''
          SELECT b.camera,b.place_id FROM frigate_camera_bindings b
          JOIN places p ON p.id=b.place_id WHERE p.active=1''')}
        for item in payload:
            event = parse_person_event(item)
            if event is None or event.camera not in bindings:
                skipped += 1
                continue
            exists = c.execute('SELECT 1 FROM frigate_object_events WHERE camera=? AND event_id=?',
                               (event.camera,event.event_id)).fetchone()
            # Known old event IDs preserve the original place and start time.
            # New events, especially LATE upstream deliveries, must use the
            # actual event timestamp, not the camera's current place.
            epoch=site_model.resolve_frigate_binding(c,event.camera,event.started_at) if not exists else None
            if not exists and epoch is None:
                skipped+=1
                unlocated+=1
                continue
            place=epoch['place_id'] if epoch is not None else bindings[event.camera]
            c.execute('''INSERT INTO frigate_object_events
              (event_id,camera,place_id,label,started_at,ended_at,score,has_snapshot,updated_at,binding_epoch_id)
              VALUES(?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(camera,event_id) DO UPDATE SET
                 -- Never alter the original place, start timestamp or epoch ID.
                 ended_at=COALESCE(excluded.ended_at,frigate_object_events.ended_at),
                 score=COALESCE(excluded.score,frigate_object_events.score),
                 has_snapshot=MAX(excluded.has_snapshot,frigate_object_events.has_snapshot),
                 updated_at=excluded.updated_at''',
              (event.event_id,event.camera,place,event.label,event.started_at,
               event.ended_at,event.score,int(event.has_snapshot),utcnow(),
               epoch['id'] if epoch else None))
            updated += int(exists is not None)
            inserted += int(exists is None)
    return {'inserted': inserted, 'updated': updated, 'skipped': skipped,
            'unlocated_before_known_binding':unlocated,
            'note': '未记录的历史机位位置不推断；已知重绑前发生的延迟事件按事件时刻归属。Frigate person目标不是独立真人身份。'}


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
    if hit.get('rule_instance_id') is not None:
        payload['rule_instance'] = {'id':hit['rule_instance_id'],
                                    'version':hit['rule_instance_version'],
                                    'parameters':hit['rule_instance_parameters']}
    return sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class SyntheticConfig(BaseModel):
    visit_gap_minutes: int = Field(default=20, ge=1, le=1440)
    encounter_window_minutes: int = Field(default=10, ge=1, le=120)
    companion_min_episodes: int = Field(default=2, ge=1, le=20)
    companion_min_people: int = Field(default=2, ge=2, le=20)
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
    watch_place_ids: list[int] = Field(default_factory=list, max_length=30)
    watch_person_ids: list[str] = Field(default_factory=list, max_length=50)
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
    result = rule_instances.apply_instances(c,result,cfg,observations,scenario_now,home)
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
        if len(cfg['watch_place_ids']) != len(set(cfg['watch_place_ids'])):
            raise HTTPException(422, '关注场所列表不得重复')
        if any(pid < 1 for pid in cfg['watch_place_ids']):
            raise HTTPException(422, '关注场所 ID 必须为正整数')
        if cfg['watch_place_ids']:
            existing = {row['id'] for row in c.execute(
                'SELECT id FROM places WHERE id IN (' +
                ','.join('?' for _ in cfg['watch_place_ids']) + ')',
                cfg['watch_place_ids'])}
            if existing != set(cfg['watch_place_ids']):
                raise HTTPException(404, '至少一个关注场所不存在')
        import re
        if len(cfg['watch_person_ids']) != len(set(cfg['watch_person_ids'])):
            raise HTTPException(422, '虚构人员关注编号不得重复')
        if any(not re.fullmatch(r'P[0-9]{3}',code) for code in cfg['watch_person_ids']):
            raise HTTPException(422, '关注人员只接受 P### 虚构编号')
        c.execute('INSERT INTO synthetic_settings(id,json_value) VALUES(1,?) '
                  'ON CONFLICT(id) DO UPDATE SET json_value=excluded.json_value',
                  (json.dumps(cfg,ensure_ascii=False),))
        c.execute('INSERT INTO synthetic_audit(operation,detail,created_at) VALUES(?,?,?)',
                  ('config_changed',json.dumps(cfg,ensure_ascii=False),utcnow()))
        result = _analyze_synthetic(c)
    return {'saved':cfg,'recomputed':True,'visit_count':result['visit_count'],
            'alert_count':len(result['alerts']),'note':'未改变原始合成事件或Frigate真实观测。'}



@app.post('/api/base/demo/duplicates')
def seed_dedup_synthetic():
    """Deterministic 100-captures-versus-three-events synthetic truth case.

    This fixture exists solely to prove that frame count does not bias a
    fictional roster. It has NO input from OpenCV, Frigate or real people.
    Idempotent event keys; never removes existing observation/history.
    """
    with db() as c:
        ids={}
        for name in ('测试场所 A','测试场所 B'):
            c.execute('INSERT OR IGNORE INTO places(name,region_id) VALUES(?,?)',
                      (name,admin.demo_leaf(c)))
            ids[name]=c.execute('SELECT id FROM places WHERE name=?',(name,)).fetchone()['id']
        start=datetime.now(timezone.utc).replace(hour=9,minute=0,second=0,microsecond=0)
        rows=[]
        for index in range(1,101):
            rows.append((f'DEDUP-A-{index:03}', 'DEDUP-A-TRACK', 'P099',
                         ids['测试场所 A'],
                         (start+timedelta(seconds=index)).isoformat(),'synthetic_only'))
        for index in range(1,4):
            rows.append((f'DEDUP-B-{index:03}', f'DEDUP-B-{index:03}', 'P099',
                         ids['测试场所 B'],
                         (start+timedelta(hours=index+1)).isoformat(),'synthetic_only'))
        before=c.total_changes
        c.executemany('''INSERT OR IGNORE INTO synthetic_observations(
            event_key,occurrence_key,person_label,place_id,observed_at,origin)
            VALUES (?,?,?,?,?,?)''',rows)
        inserted=c.total_changes-before
        c.execute('INSERT INTO synthetic_audit(operation,detail,created_at) VALUES(?,?,?)',
                  ('seed_dedup_truth_case',f'inserted={inserted}; P099 A:100/1 B:3/3',utcnow()))
        return {'mode':'synthetic_only','inserted':inserted,
                'expected':{'A_raw_observations':100,'A_appearances':1,
                            'B_raw_observations':3,'B_appearances':3,
                            'frequency_home':'测试场所 B',
                            'earliest_home':'测试场所 A'},
                'note':'P099是虚构身份，100段帧级抓拍来自确定性合成案例；并非真实视频身份聚类。'}


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


class LiveCaptureSession:
    """Keep RTSP/USB decode handle open across bounded, durable SQLite batches.

    Ownership is worker-thread-local: closing the session happens on failure,
    disable, or worker shutdown; routine successful batches never re-open RTSP.
    """

    def __init__(self, source_id: int, *, batch_frames: int = 90, sample_every: int = 10):
        self.source_id = source_id
        self.batch_frames = batch_frames
        self.sample_every = sample_every
        self._capture = None
        self._detector = None
        self._source_signature = None
        # RTSP has no immutable content digest. The ordinal indexes ingested
        # observations for this source, and must not repeat after reconnect or
        # process restart. Reserve the remainder of the last sampling stride.
        with db() as c:
            last = c.execute(
                "SELECT COALESCE(MAX(frame_index),0) FROM samples "
                "WHERE source_id=? AND origin='video'", (source_id,)).fetchone()[0]
        self._frame_base = (
            ((last - 1) // sample_every + 1) * sample_every if last else 0
        )

    def next_batch(self):
        if self._capture is None:
            with db() as c:
                source = c.execute("SELECT * FROM sources WHERE id=?", (self.source_id,)).fetchone()
                if not source or not source["enabled"]:
                    raise HTTPException(409, "视频源已停用")
                admin._active_place(c, source["place_id"])
                if source["kind"] not in ("rtsp", "camera"):
                    raise HTTPException(422, "持续采集仅支持 RTSP/摄像头")
                uri = source["uri"]
                kind = source["kind"]
                self._source_signature = (kind, uri, source["place_id"])
            capture_arg = int(uri) if kind == "camera" else uri
            self._capture = cv2.VideoCapture(capture_arg)
            if not self._capture.isOpened():
                self.close()
                raise HTTPException(400, "视频无法打开：检查摄像头权限、视频文件路径或RTSP流状态")
            try:
                self._detector = choose_detector()
            except Exception:
                self.close()
                raise
        try:
            batch = _process_source_impl(
                self.source_id, max_frames=self.batch_frames,
                sample_every=self.sample_every, worker_mode=True,
                _capture=self._capture, _detector=self._detector,
                _frame_base=self._frame_base, _expected_source=self._source_signature)
        except Exception:
            self.close()
            raise
        self._frame_base += batch["frames_decoded"]
        return batch

    def close(self):
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._detector = None
        self._source_signature = None


# The API route and persistent worker share the exact same inference/insert code;
# only the worker owns a multi-batch open capture handle.
continuous_capture.register(
    app, db,
    lambda source_id: _process_source_impl(
        source_id, max_frames=90, sample_every=10, worker_mode=True),
    make_session=LiveCaptureSession)
