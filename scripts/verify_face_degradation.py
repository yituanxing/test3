"""Controlled adverse-condition experiment on an actual decoded, licensed video.

This is a *single-scene* robustness/quality check, not an identity recognition
system or population-wide detector accuracy measurement. The public-domain
astronaut still is played as H264; transformations are applied to decoded pixels.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from app.detector import choose_detector, box_quality
from scripts.verify_face_box_truth import intersection_over_union

ROOT = Path(__file__).resolve().parents[1]


def _jpeg(frame, quality: int):
    encoded, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not encoded:
        raise RuntimeError(f'Unable to encode JPEG quality={quality}')
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError('OpenCV cannot decode its JPEG experiment')
    return image


def _pixelate(frame, factor: int):
    h,w=frame.shape[:2]
    small=cv2.resize(frame,(max(1,w//factor),max(1,h//factor)),
                     interpolation=cv2.INTER_AREA)
    return cv2.resize(small,(w,h),interpolation=cv2.INTER_NEAREST)


def _samples(frame):
    return {
        'original':frame,
        'dim_40pct':cv2.convertScaleAbs(frame,alpha=0.4,beta=0),
        'mild_gaussian_blur_3':cv2.GaussianBlur(frame,(3,3),0),
        'jpeg_quality_25':_jpeg(frame,25),
        'quarter_resolution_then_upsample':_pixelate(frame,2),
        'strong_gaussian_blur_13':cv2.GaussianBlur(frame,(13,13),0),
        'coarse_8x_pixelation':_pixelate(frame,8),
        'jpeg_quality_5':_jpeg(frame,5),
    }


def evaluate(video: Path, annotation: Path):
    source=json.loads(annotation.read_text(encoding='utf-8'))
    positive=[c for c in source['cases'] if c['reference_face_boxes_xywh']]
    if len(positive)!=1:
        raise ValueError('Experiment requires exactly one labeled positive scene')
    scene=positive[0]
    if len(scene['reference_face_boxes_xywh'])!=1:
        raise ValueError('Only exactly one manually labeled face is supported')
    sample=ROOT/scene['path']
    if sample.resolve()!=video.resolve():
        raise ValueError('Video source must match the annotated licensed sample')
    cap=cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError('Cannot open the real licensed MP4 file')
    try:
        # Decode a genuine frame rather than synthesizing an artificial face.
        ok,frame=cap.read()
        if not ok:
            raise RuntimeError('Could not decode annotated frame zero')
    finally:
        cap.release()
    H,W=frame.shape[:2]
    rw=source['coordinate_reference']['width']
    rh=source['coordinate_reference']['height']
    gt=[int(round(scene['reference_face_boxes_xywh'][0][i] *
                   ((W/rw) if i%2==0 else (H/rh)))) for i in range(4)]
    detector=choose_detector('haar')
    results=[]
    for condition, image in _samples(frame).items():
        predictions=detector.detect(image)
        scored=[]
        for predicted in predictions:
            box=[predicted[k] for k in ('x','y','w','h')]
            quality=box_quality(image,predicted)
            scored.append({'bbox_xywh':box, 'iou_to_manual_roi':round(
                intersection_over_union(box,gt),4),
                'quality_state':quality['quality_state'],
                'quality_reasons':quality['quality_reasons'],
                'sharpness_laplacian_var':quality['sharpness_laplacian_var']})
        best=max(scored,key=lambda x:x['iou_to_manual_roi'],default=None)
        results.append({
            'transformation':condition,
            'detections':len(predictions),
            'best_detection':best,
            'matched_at_iou_0_45':bool(best and best['iou_to_manual_roi']>=0.45),
        })
    byname={r['transformation']:r for r in results}
    baseline=byname['original']
    # These moderate conditions must not silently pass with background-only
    # detections. They are actionable regressions on the same licensed scene.
    required=['original','dim_40pct','mild_gaussian_blur_3',
              'jpeg_quality_25','quarter_resolution_then_upsample']
    failures=[n for n in required if not byname[n]['matched_at_iou_0_45']]
    # Severe blur must not be called a clean image by the current heuristic.
    blurred=byname['strong_gaussian_blur_13']['best_detection']
    if blurred and blurred['iou_to_manual_roi']>=0.45 and \
            blurred['quality_state'] != 'needs_review':
        failures.append('strong_blur_not_rejected_by_quality_heuristic')
    # Even if detector loses the severely blurred face, it must never show
    # it as a verified identity. That absence is explicitly recorded.
    if baseline['best_detection'] and blurred and \
            blurred['iou_to_manual_roi']>=0.45 and \
            not (blurred['sharpness_laplacian_var'] <
                 baseline['best_detection']['sharpness_laplacian_var']):
        failures.append('blur_did_not_reduce_measured_sharpness')
    return {
        'scene':scene['scene_id'],
        'distinct_source_scenes':1,
        'detector':detector.name,
        'manual_face_roi_xywh':gt,
        'decoded_resolution':[W,H],
        'moderate_conditions_required_to_match':required,
        'measurements':results,
        'failures':failures,
        'scope_note':(
            'These are eight controlled transformations of ONE positive '
            'astronaut image; they are not eight different subjects. '
            'This does not establish general face detection accuracy, '
            'biometric matching, or tracking performance.'),
    }


def run(report: Path):
    output={
        'utc':datetime.now(timezone.utc).isoformat(),
        'kind':'real_video_controlled_degradation_and_quality_state',
        'identity_recognition':False,
        'population_precision':None,
        'population_recall':None,
        'status':'FAIL',
    }
    try:
        output.update(evaluate(ROOT/'samples'/'astronaut-face-test.mp4',
                               ROOT/'samples'/'face_box_truth_v1.json'))
        if output['failures']:
            raise AssertionError('; '.join(output['failures']))
        output['status']='PASS'
        return 0
    except Exception as error:
        output['error']=f'{type(error).__name__}: {error}'
        return 1
    finally:
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(output,ensure_ascii=False,indent=2),
                          encoding='utf-8')
        print('Controlled video degradation:',output['status'],flush=True)
        if 'error' in output:print('Reason:',output['error'],flush=True)
        for item in output.get('measurements',[]):
            match=item['best_detection']
            print('  ',item['transformation'],'boxes=',item['detections'],
                  'best_iou=',match['iou_to_manual_roi'] if match else None,
                  'quality=',match['quality_state'] if match else None,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,default=ROOT/'reports'/'verify-face-degradation.json')
    raise SystemExit(run(parser.parse_args().report))
