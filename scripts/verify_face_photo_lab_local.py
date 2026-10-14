"""One ACTUAL YuNet + SFace inference smoke using public-domain sample.

This only tests 'same pixels give comparable features' and rejection of a
blank photo. It DOES NOT prove identity accuracy or commercial usage rights.
"""
import json
import os
from pathlib import Path

import cv2
import numpy as np
from fastapi import HTTPException

from app import face_photo_lab

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'samples'/'astronaut-public-domain.png'
REPORT=ROOT/'reports'/'verify-face-photo-lab-local.json'


def run():
    if not SOURCE.is_file():raise RuntimeError('Public-domain astronaut sample unavailable')
    if not face_photo_lab._ready():raise RuntimeError('SFace & YuNet not opt-in installed')
    data=SOURCE.read_bytes()
    feature1=face_photo_lab._feature(data)
    feature2=face_photo_lab._feature(data)
    same=float(np.dot(feature1,feature2))
    if same<0.999:raise AssertionError(f'Identical source repeat not stable: {same}')
    blank=cv2.imencode('.png',np.zeros((320,320,3),dtype=np.uint8))[1].tobytes()
    blank_rejected=False
    try:face_photo_lab._feature(blank)
    except HTTPException as e:blank_rejected=(e.status_code==422)
    if not blank_rejected:raise AssertionError('No-face blank image was not rejected')
    report={'test':'real_model_self_consistency_only',
            'detector':'OpenCV Zoo YuNet',
            'extractor':'OpenCV Zoo SFace 2021dec',
            'actual_pretrained_inference':True,
            'source':'public_domain_astronaut_local_sample',
            'self_similarity':round(same,5),
            'blank_image_rejected':True,
            'identity_accuracy_verified':False,
            'unconstrained_cctv_identity_verified':False,
            'photo_files_persisted':False}
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    os.environ['ZD_FACE_LAB_ENABLE']='1'
    run()
