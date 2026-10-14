"""Acceptance gate tests against representative measured TP/FP/FN.

These are controlled test fixtures, NOT evidence of model performance. The
actual real-MPEG CI report is assessed by the same script in the owner job.
"""
import copy
import pytest

from scripts.assess_real_person_readiness import InvalidEvidence, decide


def _real_report_fixture():
    def camera(name,tp,fp,fn):
        return {
            "camera_view":name,
            "decoded_frames":383,
            "real_yolo_person_detector_to_existing_bytetrack":{
                "real_pretrained_person_detector_ran":True,
                "cross_camera_identity_matching_ran":False,
                "predicted_person_boxes":tp+fp,
                "published_gt_boxes_on_inferred_frames":tp+fn,
                "detection_evaluation":{
                    "true_positives":tp,"false_positives":fp,"false_negatives":fn,
                    "evaluated_frames":24,"global_person_identity_evaluated":False,
                },
            }
        }
    return {
        "verdict":"PASS",
        "verified_cameras":[camera("corridor",48,0,0),camera("front",2,18,45)],
        "joined_two_camera_http_proof":{
            "cameras":2,"cross_camera_identity_matching_ran":False
        },
        "global_id_ground_truth":"NOT PRESENT; cross-camera ReID MUST NOT be scored",
    }


def test_actual_report_counts_are_truthfully_aggregated_as_no_go():
    result=decide(_real_report_fixture())
    assert result["real_person_identity_go_no_go"]=="NO_GO"
    assert result["cross_camera_real_reid_verified"] is False
    assert result["identity_based_real_alerts_verified"] is False
    assert result["real_person_roster_verified"] is False
    views={x["camera_view"]:x for x in result["camera_detection"]}
    assert views["corridor"]["precision_at_iou_0_5"]==1.0
    assert views["front"]["precision_at_iou_0_5"]==0.1
    assert views["front"]["recall_at_iou_0_5"]==0.0426
    assert result["global_same_different_person_ground_truth_available"] is False
    assert len(result["go_requirements_missing"])>=3


@pytest.mark.parametrize("broken",[
    lambda x:x.pop("global_id_ground_truth"),
    lambda x:x["verified_cameras"].pop(),
    lambda x:x["verified_cameras"][1]["real_yolo_person_detector_to_existing_bytetrack"]["detection_evaluation"].update(
        {"true_positives":88}),
    lambda x:x["joined_two_camera_http_proof"].update(
        {"cross_camera_identity_matching_ran":True}),
    lambda x:x["verified_cameras"][0]["real_yolo_person_detector_to_existing_bytetrack"].update(
        {"real_pretrained_person_detector_ran":False}),
    lambda x:x.update({"verdict":"ERROR"}),
])
def test_fake_or_incomplete_mediascores_cannot_be_signed_off(broken):
    fixture=copy.deepcopy(_real_report_fixture())
    broken(fixture)
    with pytest.raises(InvalidEvidence):
        decide(fixture)


def test_perfect_local_detection_is_not_real_person_identity():
    fixture=_real_report_fixture()
    for camera in fixture["verified_cameras"]:
        report=camera["real_yolo_person_detector_to_existing_bytetrack"]
        gt=report["published_gt_boxes_on_inferred_frames"]
        report["predicted_person_boxes"]=gt
        report["detection_evaluation"].update({
            "true_positives":gt,"false_positives":0,"false_negatives":0})
    result=decide(fixture)
    assert all(x["precision_at_iou_0_5"]==1 for x in result["camera_detection"])
    assert result["cross_camera_real_reid_verified"] is False
    assert result["identity_based_real_alerts_verified"] is False
