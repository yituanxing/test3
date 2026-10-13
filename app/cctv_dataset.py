"""Public, licensed fixed-CCTV demonstration using the CAVIAR research dataset.

The same *real place* has two stationary cameras. Neither a file nor a
camera-local annotation ID is a person's real-world identity; the two views
MUST NOT be counted as two venues or re-identified as one named individual.

Only an allowlisted Edinburgh University public dataset is downloadable.
MPEG2 + CVML XML may contain real people; do not pack them in the source ZIP.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import cv2
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from app import access, admin

router = APIRouter()
_db = None
_store_dir = None
MAX_VIDEO_BYTES = 30 * 1024 * 1024
MAX_XML_BYTES = 5 * 1024 * 1024
DATASET_ROOT = 'https://groups.inf.ed.ac.uk/vision/DATASETS/CAVIAR/CAVIARDATA2'
CAVIAR_LICENSE = 'CC BY-SA; credit EC Funded CAVIAR project / IST 2001 37540'
SCENARIOS = {
    'enter_exit_crossing_paths_1': {
        'title': 'EnterExitCrossingPaths1: Lisbon shopping corridor (one venue, two cameras)',
        'venue_label': '里斯本商场走廊（同一个真实场所）',
        'views': {
            'corridor': ('EnterExitCrossingPaths1cor','ceecp1gt.xml'),
            'front': ('EnterExitCrossingPaths1front','feecp1gt.xml'),
        },
    },
}


class ImportIn(BaseModel):
    place_id: int = Field(gt=0)
    scenario: str = 'enter_exit_crossing_paths_1'
    view: str = 'corridor'


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def initialize(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS public_cctv_imports (
      source_id INTEGER PRIMARY KEY REFERENCES sources(id),
      dataset TEXT NOT NULL CHECK(dataset='CAVIAR'),
      scenario TEXT NOT NULL,
      camera_view TEXT NOT NULL,
      video_sha256 TEXT NOT NULL,
      video_bytes INTEGER NOT NULL,
      video_url TEXT NOT NULL,
      annotation_url TEXT NOT NULL,
      frames_available INTEGER NOT NULL,
      xml_frame_count INTEGER NOT NULL DEFAULT 0,
      license_note TEXT NOT NULL,
      imported_at TEXT NOT NULL,
      UNIQUE(dataset,scenario,camera_view,source_id)
    );
    CREATE TABLE IF NOT EXISTS public_cctv_track_boxes (
      source_id INTEGER NOT NULL REFERENCES sources(id),
      frame_number INTEGER NOT NULL,
      camera_local_track_id TEXT NOT NULL,
      x INTEGER NOT NULL, y INTEGER NOT NULL,
      width INTEGER NOT NULL, height INTEGER NOT NULL,
      provenance TEXT NOT NULL CHECK(provenance='CAVIAR_ANNOTATION'),
      PRIMARY KEY(source_id,frame_number,camera_local_track_id)
    );
    CREATE INDEX IF NOT EXISTS cctv_boxes_track
      ON public_cctv_track_boxes(source_id,camera_local_track_id,frame_number);
    -- Inference output is a SEPARATE namespace from published CAVIAR GT.
    -- One run's local track number never means a globally unique person.
    CREATE TABLE IF NOT EXISTS model_track_runs (
      run_key TEXT PRIMARY KEY,
      source_id INTEGER NOT NULL REFERENCES sources(id),
      video_sha256 TEXT NOT NULL,
      model_name TEXT NOT NULL,
      tracker_name TEXT NOT NULL,
      params_json TEXT NOT NULL,
      frames_decoded INTEGER NOT NULL,
      frames_inferred INTEGER NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS model_runs_source ON model_track_runs(source_id);
    CREATE TABLE IF NOT EXISTS model_track_boxes (
      run_key TEXT NOT NULL REFERENCES model_track_runs(run_key),
      frame_number INTEGER NOT NULL,
      local_track_id TEXT NOT NULL,
      x INTEGER NOT NULL,y INTEGER NOT NULL,
      width INTEGER NOT NULL,height INTEGER NOT NULL,
      confidence REAL NOT NULL,
      PRIMARY KEY(run_key,frame_number,local_track_id)
    );
    CREATE INDEX IF NOT EXISTS model_track_local
      ON model_track_boxes(run_key,local_track_id,frame_number);
    """)


def register(app, db, store_dir):
    global _db, _store_dir
    _db, _store_dir = db, Path(store_dir)
    app.include_router(router)


def _state_admin(request):
    actor=access.principal(request)
    if actor is not None and actor['level'] != 1:
        raise HTTPException(403,'仅州级管理员可导入公开监控研究素材')


def _urls(scenario,view):
    data=SCENARIOS.get(scenario)
    if not data or view not in data['views']:
        raise HTTPException(422,'只允许选择公开目录内预置的 CAVIAR 场景及机位')
    stem,xml=data['views'][view]
    base=f"{DATASET_ROOT}/{stem}"
    return f"{base}/{stem}.mpg",f"{base}/{xml}"


def _fetch_allowlisted(url, max_bytes):
    # Fixed HTTPS Edinburgh host and paths ONLY, no user-provided URLs.
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='groups.inf.ed.ac.uk' or (
         not parsed.path.startswith('/vision/DATASETS/CAVIAR/CAVIARDATA2/')):
        raise HTTPException(422,'远程地址不在已审核的数据集白名单')
    request=urllib.request.Request(url,headers={'User-Agent':'ZD-CCTV-demo/1.0 (public research clip)'})
    try:
        with urllib.request.urlopen(request,timeout=35) as response:
            final=urllib.parse.urlsplit(response.geturl())
            if final.scheme!='https' or final.hostname not in (
                 'groups.inf.ed.ac.uk','homepages.inf.ed.ac.uk'):
                raise HTTPException(502,'数据站点发生非官方跨域跳转，拒绝下载')
            chunks=[]
            total=0
            while True:
                piece=response.read(min(262144,max_bytes-total+1))
                if not piece:
                    break
                total+=len(piece)
                if total>max_bytes:
                    raise HTTPException(413,'公开测试素材超过单文件限制')
                chunks.append(piece)
            if not total:
                raise HTTPException(502,'官方数据集文件为空')
            return b''.join(chunks)
    except HTTPException:
        raise
    except (OSError,TimeoutError) as exc:
        raise HTTPException(502,'官方视频站点当前不可访问；没有生成虚假的成功数据') from exc


def _annotations(xml_bytes, *, max_frames):
    """Read CAVIAR CVML XML; object ID is camera-local, never identity.

    CAVIAR frames normally begin at 1. XML files may be full-length, but
    only decoded valid video frames may be included in the stored result.
    """
    if b'<!DOCTYPE' in xml_bytes.upper() or b'<!ENTITY' in xml_bytes.upper():
        raise HTTPException(422,'不接受自定义 XML 实体')
    try:
        root=ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise HTTPException(422,'公开数据的 CVML 标注 XML 无法解析') from exc
    points=[]
    seen=set()
    frames=0
    for frame in root.iter('frame'):
        try:
            fn=int(frame.attrib['number'])
        except (KeyError,ValueError) as exc:
            raise HTTPException(422,'CVML frame 缺少合法帧号') from exc
        frames+=1
        if fn<1 or fn>max_frames:
            continue
        for obj in frame.findall('./objectlist/object'):
            oid=obj.attrib.get('id')
            box=obj.find('./box')
            if oid is None or box is None:
                continue
            try:
                xc,yc,w,h=(float(box.attrib[key]) for key in ('xc','yc','w','h'))
            except (ValueError,KeyError) as exc:
                raise HTTPException(422,'CVML 标注框字段不合法') from exc
            if w<=0 or h<=0:
                continue
            record=(fn,str(oid),max(0,round(xc-w/2)),
                    max(0,round(yc-h/2)),round(w),round(h))
            key=(fn,str(oid))
            if key not in seen:
                seen.add(key)
                points.append(record)
    return frames,points


def _probe_bytes(data: bytes, suffix='.mpg'):
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(data)
        f.flush()
        cap=cv2.VideoCapture(f.name)
        try:
            if not cap.isOpened():
                raise HTTPException(422,'官方 MPEG 文件未能由 OpenCV 解码')
            # MPEG2 program streams often report an incorrect CAP_PROP_FRAME_COUNT.
            # Actual CAVIAR CI evidence: OpenCV metadata claimed 4 frames while
            # the matching official CVML contains 383. COUNT DECODED FRAMES.
            fps=float(cap.get(cv2.CAP_PROP_FPS))
            count=0
            first=None
            while count<30000:
                ok,frame=cap.read()
                if not ok:
                    break
                if frame is None or frame.size==0:
                    continue
                if first is None:
                    first=frame
                count+=1
            if first is None or count==0:
                raise HTTPException(422,'官方 MPEG 文件未找到可解码帧')
            if count==30000:
                raise HTTPException(413,'实际视频帧数超出本地验收上限')
            return {'frames':count,'fps':round(fps,3),
                    'width':int(first.shape[1]),'height':int(first.shape[0]),
                    'frame_count_method':'actual_decoded_frames_not_unreliable_mpeg_header'}
        finally:
            cap.release()


@router.get('/api/cctv/datasets')
def dataset_catalog(request:Request):
    return {'mode':'real_fixed_camera_research_video',
            'dataset':'CAVIAR','license':CAVIAR_LICENSE,
            'scenarios':[{'id':key,'title':d['title'],'fixed_real_venue':d['venue_label'],
               'views':list(d['views'])} for key,d in SCENARIOS.items()],
            'important':'双机位拍同一走廊；标注轨迹是机位局部ID，不是人脸身份，也不证明跨场所行动。'}


@router.post('/api/cctv/datasets/import',status_code=201)
def import_video(data:ImportIn,request:Request):
    _state_admin(request)
    video_url,xml_url=_urls(data.scenario,data.view)
    with _db() as c:
        access.check_place(c,request,data.place_id)
        admin._active_place(c,data.place_id)
        # CAVIAR corridor and front are literally the same shopping hallway.
        # Never let a UI/API caller turn the two camera angles into two
        # different Excel venues and manufacture a cross-site event.
        prior_places={x['place_id'] for x in c.execute("""SELECT DISTINCT s.place_id
            FROM public_cctv_imports i JOIN sources s ON i.source_id=s.id
            WHERE i.scenario=?""",(data.scenario,))}
        if prior_places and prior_places!={data.place_id}:
            raise HTTPException(409,'该 CAVIAR 真实走廊已经绑定一个固定场所；双机位必须属于同一场所')
        existing=c.execute('''SELECT i.*,s.name FROM public_cctv_imports i
            JOIN sources s ON i.source_id=s.id
            WHERE i.scenario=? AND i.camera_view=? AND s.place_id=?
            ORDER BY i.imported_at DESC LIMIT 1''',
            (data.scenario,data.view,data.place_id)).fetchone()
        if existing and c.execute('SELECT uri FROM sources WHERE id=?',
                                  (existing['source_id'],)).fetchone():
            stored=c.execute('SELECT uri FROM sources WHERE id=?',
                             (existing['source_id'],)).fetchone()['uri']
            if Path(stored).is_file():
                return {'source_id':existing['source_id'],'status':'already_imported',
                        'camera_view':data.view,'place_id':data.place_id,
                        'mode':'real_fixed_camera_research_video',
                        'annotation_boxes':c.execute('SELECT COUNT(*) FROM public_cctv_track_boxes WHERE source_id=?',
                                                     (existing['source_id'],)).fetchone()[0]}
    video=_fetch_allowlisted(video_url,MAX_VIDEO_BYTES)
    annotation=_fetch_allowlisted(xml_url,MAX_XML_BYTES)
    dimensions=_probe_bytes(video)
    frame_count,boxes=_annotations(annotation,max_frames=dimensions['frames'])
    digest=hashlib.sha256(video).hexdigest()
    _store_dir.mkdir(parents=True,exist_ok=True)
    dest=_store_dir/(digest[:24]+'.mpg')
    if not dest.exists():
        tmp=_store_dir/(digest[:24]+'.partial')
        try:
            with tmp.open('wb') as f:
                f.write(video)
            os.replace(tmp,dest)
        finally:
            tmp.unlink(missing_ok=True)
    with _db() as c:
        access.check_place(c,request,data.place_id)
        admin._active_place(c,data.place_id)
        # Every camera is a source; BOTH map to the same place_id. Any
        # caller choosing different places has to configure that intentionally,
        # not because 'front' looks like another 'venue'.
        src=c.execute('INSERT INTO sources(place_id,name,kind,uri) VALUES(?,?,?,?)',
                      (data.place_id,
                       f"CAVIAR {data.scenario} / {data.view} 固定摄像机",
                       'file',str(dest)))
        from app import site_model
        site_model.record_initial(c,src.lastrowid,data.place_id)
        c.execute('''INSERT INTO public_cctv_imports(source_id,dataset,scenario,camera_view,
                  video_sha256,video_bytes,video_url,annotation_url,
                  frames_available,xml_frame_count,license_note,imported_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (src.lastrowid,'CAVIAR',data.scenario,data.view,digest,len(video),
                   video_url,xml_url,dimensions['frames'],frame_count,CAVIAR_LICENSE,_utc()))
        c.executemany('''INSERT INTO public_cctv_track_boxes
                   (source_id,frame_number,camera_local_track_id,x,y,width,height,provenance)
                   VALUES(?,?,?,?,?,?,?,'CAVIAR_ANNOTATION')''',
                   ((src.lastrowid,*row) for row in boxes))
        admin.audit(c,'caviar_public_dataset_import',f"source:{src.lastrowid}",None,
                    {'dataset':'CAVIAR','view':data.view,'place_id':data.place_id,
                     'video_bytes':len(video),'annotation_boxes':len(boxes)})
        return {'status':'imported','source_id':src.lastrowid,
                'place_id':data.place_id,'camera_view':data.view,
                'video':dimensions,'annotation_boxes':len(boxes),
                'xml_frames':frame_count,'license':CAVIAR_LICENSE,
                'mode':'real_fixed_camera_research_video',
                'track_semantics':'camera_local_ground_truth_not_global_identity'}


@router.get('/api/cctv/sources')
def imported_sources(request:Request):
    with _db() as c:
        allowed={p['id'] for p in admin.place_rows(
          c,access.effective_region(c,request,None))}
        items=[dict(x) for x in c.execute('''SELECT i.*,s.place_id,s.name,p.name AS place_name
            FROM public_cctv_imports i JOIN sources s ON s.id=i.source_id
            JOIN places p ON p.id=s.place_id ORDER BY i.source_id DESC''')
               if x['place_id'] in allowed]
        for item in items:
            item['video_sha256']=item['video_sha256'][:16]+'…'
            item['local_track_count']=c.execute('''SELECT COUNT(DISTINCT camera_local_track_id)
                FROM public_cctv_track_boxes WHERE source_id=?''',
                (item['source_id'],)).fetchone()[0]
            item['annotation_boxes']=c.execute('SELECT COUNT(*) FROM public_cctv_track_boxes WHERE source_id=?',
                                               (item['source_id'],)).fetchone()[0]
        return {'mode':'real_cctv_annotated','sources':items,
                'counting_unit':'per_camera_local_tracks_not_unique_people'}


@router.get('/api/cctv/sources/{source_id}/activity')
def camera_activity(source_id:int,request:Request,
                    limit:int=Query(100,ge=1,le=500)):
    with _db() as c:
        access.check_source(c,request,source_id)
        entry=c.execute('SELECT * FROM public_cctv_imports WHERE source_id=?',
                        (source_id,)).fetchone()
        if not entry:
            raise HTTPException(404,'该源不是已核验的CAVIAR固定摄像机素材')
        tracks=[dict(x) for x in c.execute('''SELECT camera_local_track_id,
             COUNT(*) AS annotated_frames,MIN(frame_number) AS first_frame,
             MAX(frame_number) AS last_frame FROM public_cctv_track_boxes
             WHERE source_id=? GROUP BY camera_local_track_id
             ORDER BY annotated_frames DESC LIMIT ?''',(source_id,limit))]
        return {'source_id':source_id,'camera_view':entry['camera_view'],
                'dataset':'CAVIAR','mode':'research_ground_truth',
                'local_tracks':tracks,'local_track_count':len(tracks),
                'frame_annotation_count':c.execute('''SELECT COUNT(*) FROM public_cctv_track_boxes
                     WHERE source_id=?''',(source_id,)).fetchone()[0],
                'license':entry['license_note'],
                'warning':'单摄像头人工标注轨迹不是唯一自然人；两个摄像头计数不可相加为去重人数，未做跨镜身份关联或风险认定。'}

@router.get('/api/cctv/sources/{source_id}/tracking-baseline')
def tracking_baseline(source_id:int,request:Request,
                      max_frames:int=Query(400,ge=1,le=600),
                      algorithm:Literal["bytetrack","ocsort"]="bytetrack"):
    """Real existing tracking algorithms on published camera GT, not ReID.

    Optional tracker dependency absent => explicit 503. Never silently fake
    tracking IDs or contaminate real Frigate counts or simulated P### roster.
    """
    from app.tracker_adapters import run_caviar_tracking
    with _db() as c:
        access.check_source(c,request,source_id)
        return run_caviar_tracking(c,source_id,max_frames=max_frames,algorithm=algorithm)


@router.get('/api/cctv/sources/{source_id}/person-detector-baseline')
def person_detector_baseline(source_id:int,request:Request,
                             max_frames:int=Query(32,ge=1,le=64),
                             sample_every:int=Query(1,ge=1,le=4),
                             confidence:float=Query(.25,ge=.1,le=.9)):
    """Real pretrained YOLO + mature ByteTrack, NOT GT-injected tracking.

    Optional dependency; no user-defined model filename or arbitrary URL.
    Source video must be the exact originally imported SHA256 source.
    """
    from app.person_detection_adapter import run_real_person_detector
    with _db() as c:
        access.check_source(c,request,source_id)
        return run_real_person_detector(c,source_id,max_frames=max_frames,
                 sample_every=sample_every,confidence=confidence)


@router.get('/api/cctv/sources/{source_id}/pair-tracking-baseline')
def compare_caviar_pair(source_id:int,request:Request,
                        max_frames:int=Query(400,ge=1,le=600)):
    """ONE venue, TWO cameras, TWO mature trackers; ZERO inferred global IDs.

    This is not two venues and camera-local GT object IDs are not shared
    identity labels. Do not emit pairwise matches without ground-truth IDs.
    """
    from app.tracker_adapters import run_caviar_tracking
    with _db() as c:
        access.check_source(c,request,source_id)
        selected=c.execute("""SELECT s.place_id,i.dataset,i.scenario FROM sources s
          JOIN public_cctv_imports i ON i.source_id=s.id WHERE s.id=?""",
          (source_id,)).fetchone()
        if selected is None:
            raise HTTPException(404,'先导入官方CAVIAR固定摄像机素材')
        pair=[dict(r) for r in c.execute("""SELECT s.id,i.camera_view
          FROM sources s JOIN public_cctv_imports i ON i.source_id=s.id
          WHERE s.place_id=? AND i.dataset=? AND i.scenario=?
          ORDER BY i.camera_view,s.id""",
          (selected['place_id'],selected['dataset'],selected['scenario']))]
        views={r['camera_view'] for r in pair}
        if len(pair)!=2 or views!={'corridor','front'}:
            raise HTTPException(409,'场所内必须导入两个官方固定机位，才能进行双机位跟踪实验')
        for camera in pair:
            access.check_source(c,request,camera['id'])
        results=[]
        for camera in pair:
            results.append({'camera_view':camera['camera_view'],
                 'source_id':camera['id'],
                 'algorithms':[
                     run_caviar_tracking(c,camera['id'],max_frames=max_frames,
                                         algorithm=algorithm)
                     for algorithm in ('bytetrack','ocsort')]})
        return {
            'mode':'existing_trackers_two_camera_one_venue_research_baseline',
            'place_id':selected['place_id'],
            'scenario':selected['scenario'],
            'cameras':results,
            'algorithms':['bytetrack','ocsort'],
            'camera_count':2,
            'automatic_camera_local_tracking_ran':True,
            'automatic_person_detection_ran':False,
            'cross_camera_identity_matching_ran':False,
            'shared_global_identity_ground_truth':False,
            'counts_as_independent_real_people':False,
            'eligible_for_real_roster_or_alerts':False,
            'note':'对同一走廊两个摄像机分别使用现成ByteTrack和OC-SORT评估本机跟踪，严格不根据本机轨迹编号断言跨镜头同一自然人。研究素材没有足够的全局身份真值。',
        }


@router.get('/api/cctv/sources/{source_id}/frame')
def annotated_frame(source_id:int,request:Request,
                    frame:int=Query(1,ge=1,le=25000)):
    """Actual decoded MPEG picture plus ORIGINAL manual CVML boxes.

    Labels are camera-local tracking IDs. No HOG/YOLO/face recognition
    is claimed here; manual bounding boxes are original published truth.
    """
    with _db() as c:
        access.check_source(c,request,source_id)
        row=c.execute('''SELECT i.frames_available,s.uri FROM public_cctv_imports i
          JOIN sources s ON s.id=i.source_id WHERE i.source_id=?''',
          (source_id,)).fetchone()
        if not row:
            raise HTTPException(404,'该源不存在公开CAVIAR视频版本')
        if frame>row['frames_available']:
            raise HTTPException(422,'帧号超出该视频标注范围')
        boxes=[dict(x) for x in c.execute('''SELECT camera_local_track_id,x,y,width,height
            FROM public_cctv_track_boxes WHERE source_id=? AND frame_number=?
            ORDER BY camera_local_track_id''',(source_id,frame))]
    capture=cv2.VideoCapture(row['uri'])
    try:
        if not capture.isOpened():
            raise HTTPException(409,'已登记CAVIAR视频文件目前不可读')
        # MPEG2 random-access frame indexes may be unreliable, too.
        # On the bounded tiny proof clip, decoding sequentially is exact.
        picture=None
        for index in range(frame):
            ok,picture=capture.read()
            if not ok:
                raise HTTPException(409,'指定的监控帧目前无法解码')
        if picture is None:
            raise HTTPException(409,'指定的监控帧目前无法解码')
        for box in boxes:
            x,y,w,h=box['x'],box['y'],box['width'],box['height']
            cv2.rectangle(picture,(x,y),(x+w,y+h),(40,235,100),2)
            cv2.putText(picture,'local#'+box['camera_local_track_id'],
                        (x,max(11,y-4)),cv2.FONT_HERSHEY_SIMPLEX,
                        0.35,(30,235,100),1,cv2.LINE_AA)
        # This watermark is in the output and cannot be mistaken for an
        # inference result or a named physical-world identity.
        cv2.rectangle(picture,(0,0),(picture.shape[1],24),(22,22,22),-1)
        cv2.putText(picture,'CAVIAR GT annotated / camera-local IDs',
                    (3,16),cv2.FONT_HERSHEY_SIMPLEX,0.37,(255,255,255),1)
        ok,jpg=cv2.imencode('.jpg',picture,[cv2.IMWRITE_JPEG_QUALITY,82])
        if not ok:
            raise HTTPException(500,'当前监控帧 JPEG 编码失败')
        return Response(content=jpg.tobytes(),media_type='image/jpeg',
                headers={'Cache-Control':'no-store',
                    'X-Annotations':'CAVIAR manual ground truth',
                    'X-Camera-Local-Boxes':str(len(boxes))})
    finally:
        capture.release()
