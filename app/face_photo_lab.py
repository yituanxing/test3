"""Opt-in short-lived consented photograph similarity laboratory.

NOT an integrated CCTV identity system. No file, biometric feature, match or
identifier is persisted in SQLite or cross-fed into EPFL/Frigate/P###. Only
explicitly submitted, consent-confirmed images are compared. SFace ranks
possible similarity; it does not make identity or legal claims.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.detector import MODEL_FILE as YUNET_MODEL

router=APIRouter()
ROOT=Path(__file__).resolve().parents[1]
SFACE_MODEL=ROOT/'models'/'face_recognition_sface_2021dec.onnx'
MAX_IMAGE_BYTES=2_000_000
MAX_GALLERY=16
TTL_SECONDS=900
_storage={}
_lock=threading.RLock()

POLICY=('只可使用拥有明确授权或本人提供的照片。仅比较本次临时上传的图片，'
        '相似度得分不代表身份确认；不连接视频监控、真人底库或预警。'
        '无人工审核的自动识别/风险判断不在此功能范围。')


def enabled():
    return os.getenv('ZD_FACE_LAB_ENABLE')=='1'


def _actor(request):
    actor=getattr(request.state,'principal',None)
    if not actor or actor['level']!=1:
        raise HTTPException(403,'本地照片实验仅州级授权管理员可访问')
    return actor


def _ready():
    return enabled() and YUNET_MODEL.is_file() and SFACE_MODEL.is_file()


def _key(request):
    _actor(request)
    token=request.headers.get('authorization','')
    if not token.lower().startswith('bearer ') or len(token)<32:
        raise HTTPException(401,'请使用实际登录会话')
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def _consent(request):
    if request.headers.get('x-lab-consent')!='yes':
        raise HTTPException(403,'必须明确确认照片拥有合法授权并用于本次短时实验')


def _evict():
    now=time.monotonic()
    for key in list(_storage):
        if _storage[key]['expires']<now:
            del _storage[key]


def _feature(image:bytes):
    """Pretrained YuNet exact-one-face gate + SFace cosine embedding.

    Both local weights must already exist, or return a truthful 503.
    Images never leave memory and are never written to files.
    """
    if not _ready():
        raise HTTPException(503,'实验未启用或缺少YuNet/SFace模型，参见本页部署说明')
    import cv2
    if len(image)<100 or len(image)>MAX_IMAGE_BYTES:
        raise HTTPException(413,'图像文件须在100字节至2MB之间')
    arr=cv2.imdecode(np.frombuffer(image,dtype=np.uint8),cv2.IMREAD_COLOR)
    if arr is None:
        raise HTTPException(422,'不是可解码的JPEG/PNG图片')
    height,width=arr.shape[:2]
    if not 80<=height<=2200 or not 80<=width<=2200:
        raise HTTPException(422,'图片尺寸须在80到2200像素之间')
    try:
        detector=cv2.FaceDetectorYN.create(str(YUNET_MODEL),'',(width,height),
                                            score_threshold=.9,
                                            nms_threshold=.3,top_k=20)
        _,faces=detector.detect(arr)
    except cv2.error as exc:
        raise HTTPException(503,'YuNet人脸检测运行失败') from exc
    count=0 if faces is None else len(faces)
    if count!=1:
        raise HTTPException(422,f'需要恰好一张可检测人脸，当前检测到{count}张；不自动选择多个人')
    if min(float(faces[0,2]),float(faces[0,3]))<55:
        raise HTTPException(422,'照片中的人脸过小，不能进行可靠的相似度演示')
    try:
        recog=cv2.FaceRecognizerSF.create(str(SFACE_MODEL),'')
        crop=recog.alignCrop(arr,faces[0])
        feature=np.asarray(recog.feature(crop),dtype=np.float32).reshape(-1)
    except cv2.error as exc:
        raise HTTPException(503,'SFace特征推理失败') from exc
    norm=float(np.linalg.norm(feature))
    if not np.isfinite(feature).all() or norm<1e-9 or feature.size<64:
        raise HTTPException(422,'人脸特征无效，拒绝入库')
    return feature/norm


def register(app):
    app.include_router(router)


@router.get('/face-lab')
def interface():
    return FileResponse(ROOT/'static'/'face-lab.html')


@router.get('/api/face-lab/status')
def status(request:Request):
    key=_key(request)
    with _lock:
        _evict()
        session=_storage.get(key,{'samples':{}})
        return {'mode':'consented_photo_lab_only','enabled':enabled(),
                'ready':_ready(),'has_yunet':YUNET_MODEL.is_file(),
                'has_sface':SFACE_MODEL.is_file(),'expires_after_seconds':TTL_SECONDS,
                'max_samples':MAX_GALLERY,'count':len(session['samples']),
                'sample_codes':list(session['samples']),'disclaimer':POLICY}


@router.post('/api/face-lab/enroll')
async def enroll(request:Request,code:str=Query(...,pattern=r'^S[A-Za-z0-9_-]{1,15}$')):
    key=_key(request)
    _consent(request)
    if request.headers.get('content-type','').split(';')[0] not in ('image/jpeg','image/png'):
        raise HTTPException(415,'只能上传JPEG或PNG')
    if int(request.headers.get('content-length') or 0)>MAX_IMAGE_BYTES:
        raise HTTPException(413,'图片超过2MB')
    image=await request.body()
    if len(image)>MAX_IMAGE_BYTES:
        raise HTTPException(413,'图片超过2MB')
    feature=_feature(image)
    with _lock:
        _evict()
        row=_storage.setdefault(key,{'samples':{},'expires':0})
        if code not in row['samples'] and len(row['samples'])>=MAX_GALLERY:
            raise HTTPException(409,'本会话最多允许16份授权样本，请清空后重新录入')
        row['samples'][code]=feature
        row['expires']=time.monotonic()+TTL_SECONDS
        count=len(row['samples'])
    return {'sample_code':code,'gallery_count':count,'photo_stored':False,
            'embedding_temporary':True,'identity_confirmed':False,'disclaimer':POLICY}


@router.post('/api/face-lab/search')
async def search(request:Request):
    key=_key(request)
    _consent(request)
    if request.headers.get('content-type','').split(';')[0] not in ('image/jpeg','image/png'):
        raise HTTPException(415,'只能上传JPEG或PNG')
    if int(request.headers.get('content-length') or 0)>MAX_IMAGE_BYTES:
        raise HTTPException(413,'图片超过2MB')
    img=await request.body()
    if len(img)>MAX_IMAGE_BYTES:raise HTTPException(413,'图片超过2MB')
    query=_feature(img)
    with _lock:
        _evict()
        gallery=_storage.get(key,{'samples':{}})['samples']
        results=[{'sample_code':code,
                  'cosine_similarity':round(float(np.dot(query,feat)),5),
                  'identity_confirmed':False} for code,feat in gallery.items()]
        results.sort(key=lambda r:(-r['cosine_similarity'],r['sample_code']))
        if key in _storage:_storage[key]['expires']=time.monotonic()+TTL_SECONDS
    return {'mode':'consented_photo_lab_only','ranked_candidates':results[:MAX_GALLERY],
            'total_candidates':len(results),'unverified_identity':True,
            'decision':'similarity_ranking_only_requires_manual_review',
            'disclaimer':POLICY}


@router.post('/api/face-lab/clear')
def clear(request:Request):
    key=_key(request)
    with _lock:
        deleted=len(_storage.pop(key,{'samples':{}})['samples'])
    return {'cleared_temporary_embeddings':deleted,'remaining':0,'note':'图片原始字节未持久化'}
