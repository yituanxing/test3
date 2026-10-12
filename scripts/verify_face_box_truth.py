"""Check REAL face box localization against manually marked public sample.

This verifier does not estimate general population precision/recall. It contains
only ONE distinct positive still image and ONE negative test-pattern scene,
sampled at several temporal points to verify stable decoding/model behavior.
No real-person identity, embeddings, or tracking are involved.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import cv2

from app.detector import choose_detector


ROOT = Path(__file__).resolve().parents[1]
ANNOTATIONS = ROOT / 'samples' / 'face_box_truth_v1.json'


def intersection_over_union(a, b):
    """Compare two xywh boxes; one is a manual face ROI, one is a detection."""
    ax, ay, aw, ah = (int(v) for v in a)
    bx, by, bw, bh = (int(v) for v in b)
    if min(aw, ah, bw, bh) <= 0:
        return 0.0
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    common = max(0, right - left) * max(0, bottom - top)
    union = aw * ah + bw * bh - common
    return common / union if union > 0 else 0.0


def _boxes(frame, detector):
    return [[int(b[c]) for c in ('x', 'y', 'w', 'h')] for b in detector.detect(frame)]


def evaluate_case(case, detector, ref_size, min_iou):
    path = ROOT / case['path']
    if not path.is_file():
        raise FileNotFoundError('Required annotated video missing: ' + str(path))
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError('OpenCV cannot open truth video: ' + str(path))
    records = []
    try:
        for index in case['frame_indices_zero_based']:
            assert isinstance(index, int) and index >= 0
            assert cap.set(cv2.CAP_PROP_POS_FRAMES, index), (
                'Video decoder cannot seek to annotated frame ' + str(index))
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f'Cannot decode {path.name} at annotated frame {index}')
            height, width = frame.shape[:2]
            sx, sy = width / ref_size['width'], height / ref_size['height']
            gt = [[int(round(x*sx)), int(round(y*sy)),
                   int(round(w*sx)), int(round(h*sy))]
                  for x, y, w, h in case['reference_face_boxes_xywh']]
            predicted = _boxes(frame, detector)
            # One-to-one geometry matching: a single GT cannot be credited
            # multiple times if the detector creates duplicate face boxes.
            potential = sorted(
                ((intersection_over_union(ground, pred), gi, pi)
                 for gi, ground in enumerate(gt)
                 for pi, pred in enumerate(predicted)), reverse=True)
            matched_gt, matched_predictions = set(), set()
            matches = []
            for iou, gi, pi in potential:
                if iou < min_iou:
                    break
                if gi not in matched_gt and pi not in matched_predictions:
                    matched_gt.add(gi)
                    matched_predictions.add(pi)
                    matches.append({'ground_truth_index':gi,'prediction_index':pi,
                                    'iou':round(float(iou),4)})
            records.append({
                'frame_index_zero_based':index,
                'frame_width':width,'frame_height':height,
                'gt_boxes_xywh':gt,'predicted_boxes_xywh':predicted,
                'matched':matches,
                'localized_faces':len(matches),
                'missed_faces':len(gt)-len(matches),
                'unmatched_predicted_boxes':len(predicted)-len(matches),
            })
    finally:
        cap.release()
    return {
        'scene':case['scene_id'],'label':case['label'],'source':case['path'],
        'distinct_source_images':1,  # both bundled videos repeat the same scene
        'frames':records,
    }


def run(report_path: Path, annotation_path: Path = ANNOTATIONS, min_iou=0.45):
    report = {
        'checked_at_utc':datetime.now(timezone.utc).isoformat(),
        'task':'face_detection_box_localization_on_two_manually_labeled_scenes',
        'detector':'opencv-haar',
        'minimum_match_iou':min_iou,
        'ground_truth_source':str(annotation_path.relative_to(ROOT))
            if annotation_path.is_relative_to(ROOT) else str(annotation_path),
        'contains_identity_recognition':False,
        'representative_benchmark':False,
        'calibrated_population_precision':None,
        'calibrated_population_recall':None,
        'verdict':'FAIL',
    }
    try:
        manifest=json.loads(annotation_path.read_text(encoding='utf-8'))
        if manifest.get('schema_version') != '1.0':
            raise ValueError('Unsupported face truth manifest schema')
        scenes=manifest['cases']
        if len(scenes)!=2 or len({c['scene_id'] for c in scenes})!=2:
            raise ValueError('Need exactly the one positive and one negative annotated scene')
        positive=[c for c in scenes if c['reference_face_boxes_xywh']]
        negative=[c for c in scenes if not c['reference_face_boxes_xywh']]
        if len(positive)!=1 or len(negative)!=1:
            raise ValueError('Truth set must include positive and negative independent scenes')
        detector=choose_detector('haar')
        results=[evaluate_case(case,detector,manifest['coordinate_reference'],min_iou)
                 for case in scenes]
        report['scenes']=results
        pos=next(c for c in results if c['label']==positive[0]['label'])
        neg=next(c for c in results if c['label']==negative[0]['label'])
        pos_matched=sum(x['localized_faces'] for x in pos['frames'])
        pos_missed=sum(x['missed_faces'] for x in pos['frames'])
        positive_extras=sum(x['unmatched_predicted_boxes'] for x in pos['frames'])
        negative_false=sum(x['unmatched_predicted_boxes'] for x in neg['frames'])
        report['checks']={
            'positive_labeled_frames':len(pos['frames']),
            'positive_frame_face_matches':pos_matched,
            'positive_frame_face_misses':pos_missed,
            'false_boxes_in_positive_scene':positive_extras,
            'false_boxes_in_no_face_scene':negative_false,
            'distinct_positive_scenes':1,'distinct_negative_scenes':1,
        }
        report['limitations'] = (
            'One marked NASA astronaut still and one non-human test-pattern; '
            'multiple video frames show the SAME content. Face box IoU is a '
            'geometry smoke test, not validated detector sensitivity on people. '
            'No demographic or multi-camera accuracy is inferred.')
        assert pos_matched==len(pos['frames']), (
            f'Manually marked face ROI missed on {pos_missed} positive frame(s)')
        assert positive_extras==0, (
            f'{positive_extras} additional unmatched detection(s) in positive still')
        assert negative_false==0, (
            f'{negative_false} false face rectangle(s) in no-human video')
        report['verdict']='PASS'
        return 0
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)
        return 1
    finally:
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False),
                               encoding='utf-8')
        print('Annotated geometry verdict:',report['verdict'], flush=True)
        print('Annotated geometry evidence:',report_path, flush=True)
        if 'error' in report:
            print('Annotated geometry failure:',report['error'],flush=True)
        elif 'checks' in report:
            print(json.dumps(report['checks'],ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,
                        default=ROOT/'reports'/'verify-face-box-truth.json')
    args=parser.parse_args()
    raise SystemExit(run(args.report))
