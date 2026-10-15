"""Make the Excel real-identity go/no-go decision from ACTUAL CAVIAR CI evidence.

This is a transparent demo acceptance *decision*, not a face-ID algorithm.
It never infers that two camera-local tracks are one real human.

Usage:
    python -m scripts.assess_real_person_readiness \
        --input reports/verify-caviar-fixed-cctv.json \
        --output reports/real-person-identity-readiness.json

Return 0 when a truthful decision report was produced (including NO_GO).
Return nonzero if the media evidence is missing, contradictory or malformed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


class InvalidEvidence(ValueError):
    pass


def decide(evidence: dict) -> dict:
    if evidence.get("verdict") != "PASS":
        raise InvalidEvidence("Real-media validation has not succeeded")
    cameras = evidence.get("verified_cameras", [])
    if len(cameras) != 2 or {c.get("camera_view") for c in cameras} != {"corridor","front"}:
        raise InvalidEvidence("Expected real corridor + front CAVIAR cameras")
    measurements=[]
    for camera in cameras:
        pred=camera.get("real_yolo_person_detector_to_existing_bytetrack") or {}
        ev=pred.get("detection_evaluation") or {}
        for field in ("true_positives","false_positives","false_negatives","evaluated_frames"):
            if type(ev.get(field)) is not int or ev[field]<0:
                raise InvalidEvidence(f"Missing real {camera['camera_view']} {field}")
        tp,fp,fn=(ev[x] for x in ("true_positives","false_positives","false_negatives"))
        if ev["evaluated_frames"] <= 0 or tp+fp!=pred.get("predicted_person_boxes"):
            raise InvalidEvidence(f"Invalid prediction bookkeeping: {camera['camera_view']}")
        if tp+fn!=pred.get("published_gt_boxes_on_inferred_frames"):
            raise InvalidEvidence(f"Invalid independent GT count: {camera['camera_view']}")
        precision=tp/(tp+fp) if tp+fp else None
        recall=tp/(tp+fn) if tp+fn else None
        if ev.get("global_person_identity_evaluated") is not False:
            raise InvalidEvidence("Detection metrics must not be called global ReID evaluation")
        if pred.get("real_pretrained_person_detector_ran") is not True:
            raise InvalidEvidence(f"No real pretrained detector on {camera['camera_view']}")
        if pred.get("cross_camera_identity_matching_ran") is not False:
            raise InvalidEvidence("Unexpected cross-camera identity assertion")
        measurements.append({
            "camera_view":camera["camera_view"],
            "decoded_frames":camera.get("decoded_frames"),
            "independent_yolo_inferred_frames":ev["evaluated_frames"],
            "tp":tp,"fp":fp,"fn":fn,
            "precision_at_iou_0_5":round(precision,4) if precision is not None else None,
            "recall_at_iou_0_5":round(recall,4) if recall is not None else None,
            "global_reid_validated":False
        })
    paired=evidence.get("joined_two_camera_http_proof") or {}
    if (paired.get("cameras")!=2
        or paired.get("cross_camera_identity_matching_ran") is not False
        or evidence.get("global_id_ground_truth") is None):
        raise InvalidEvidence("Two-camera proof does not distinguish tracking from identity")
    # No global same/different-person ground truth, no cross-camera ReID
    # evaluation, no reliable target identity. A camera-local tracking score,
    # even a perfect detection TP/FP tally, cannot promote a person into
    # an identity-based roster or issue real cross-site alerts.
    return {
        "contract":"excel_demo_2026_real_person_gate_v1",
        "reference":"actual_public_CAVIAR_camera_local_and_independent_model_evaluation",
        "test_media":"two real university CAVIAR MPEG camera views",
        "real_person_identity_go_no_go":"NO_GO",
        "cross_camera_real_reid_verified":False,
        "identity_based_real_alerts_verified":False,
        "real_person_roster_verified":False,
        "camera_local_tracking_verified":True,
        "pretrained_person_detector_verified_on_bounded_real_frames":True,
        "camera_detection":measurements,
        "global_same_different_person_ground_truth_available":False,
        "go_requirements_missing":[
            "A legally usable dataset with synchronized camera views and shared global identity ground truth",
            "True same-person AND different-person match evaluation with reported false-merge rate",
            "A real, evaluated pretrained ReID model running on actual detector track crops",
            "Audited promotion from verified global identity to authorized real visits and risk rules",
        ],
        "delivery_scope":"real video -> detector -> camera-local track -> QA -> evidence and statistics; "
                         "separate synthetic P### -> roster -> seven scenario rules -> alert UI; "
                         "operator-declared D### research links are unverified study sessions",
        "must_not_claim":[
            "CAVIAR published same-looking camera-local track IDs prove the same human",
            "YOLO person detection precision or local ByteTrack IDs equal face recognition",
            "One study D### link makes a legally verified natural person",
            "Synthetic seven-rule alerts were triggered by identifiable people in real footage",
        ],
        "next_existing_solution":"BoxMOT pretrained OSNet/FastReID on a global-ID truth dataset; "
                                 "candidate-only until calibrated; do not replace existing ByteTrack",
        "recommendation":"Freeze and deliver honestly scoped first-version Demo. "
                         "Do not block real/synthetic functional demonstration on unproven global identity."
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    decision=decide(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(decision,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"decision":decision["real_person_identity_go_no_go"],
                      "cross_camera_real_reid_verified":False,
                      "cameras":decision["camera_detection"],
                      "report":str(args.output)},ensure_ascii=False))


if __name__=="__main__":
    main()
