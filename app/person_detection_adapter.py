"""Existing YOLO11 person detector -> existing Roboflow ByteTrack on REAL CAVIAR frames.

Optional research-only dependency: Ultralytics YOLO11n (AGPL-3.0 or
commercial license) + Roboflow trackers. No custom detector/tracker, biometric
identity, automatically inferred cross-camera pairing, or P### roster feed.

Unlike the published-CVML-to-ByteTrack upper-bound benchmark, detections
here come entirely from pretrained YOLO, *not* official manual boxes. Manual
CVML boxes are retained as independent evaluation evidence only.
"""
from __future__ import annotations

from functools import lru_cache
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock

import cv2
from fastapi import HTTPException

from app import tracker_adapters

MODEL = "yolo11n.pt"
MODEL_LICENSE = "Ultralytics YOLO11n: AGPL-3.0 / Enterprise (review before commercial use)"
_lock = Lock()


@lru_cache(maxsize=1)
def _model():
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise HTTPException(503,
            "未安装现成的 Ultralytics YOLO11n；请安装 requirements-person-detection.txt") from exc
    return YOLO(MODEL)


def run_real_person_detector(c,source_id:int,*,max_frames:int=32,
                             sample_every:int=1,confidence:float=.25):
    if not 1<=max_frames<=64 or not 1<=sample_every<=4 or not .1<=confidence<=.9:
        raise ValueError("bounded detection parameters required")
    record=c.execute("""SELECT i.video_sha256,i.frames_available,i.camera_view,
            i.scenario,s.uri,s.place_id FROM public_cctv_imports i
            JOIN sources s ON s.id=i.source_id WHERE i.source_id=?""",
            (source_id,)).fetchone()
    if not record:
        raise HTTPException(404,"该场所没有已导入的CAVIAR实视频")
    file=Path(record["uri"])
    if not file.is_file() or sha256(file.read_bytes()).hexdigest()!=record["video_sha256"]:
        raise HTTPException(409,"视频文件缺失或内容SHA256不匹配；不产生虚假的检测结果")
    sv,trackers,np=tracker_adapters._dependencies()
    model=_model()
    weights=Path(getattr(model,"ckpt_path",MODEL) or MODEL)
    weights_sha=sha256(weights.read_bytes()).hexdigest() if weights.is_file() else None
    # Use the SAME minimum confidence contract as YOLO's public detector:
    # the previous .50/.50 tracker gates discarded 19 of 20 actual YOLO
    # detections on the 2nd CAVIAR camera in the real-media CI. ByteTrack is
    # still the existing vendor implementation; lower-confidence outputs
    # remain unverified camera-local candidates requiring QA, never IDs.
    activation_threshold=confidence
    tracker=trackers.ByteTrackTracker(
        minimum_consecutive_frames=1,
        track_activation_threshold=activation_threshold,
        high_conf_det_threshold=activation_threshold)
    stop=min(max_frames,int(record["frames_available"]))
    gt_counts=dict(c.execute("""SELECT frame_number,COUNT(*) FROM public_cctv_track_boxes
        WHERE source_id=? AND frame_number<=? GROUP BY frame_number""",
        (source_id,stop)).fetchall())
    cap=cv2.VideoCapture(str(file))
    if not cap.isOpened():
        raise HTTPException(409,"官方CAVIAR实视频无法打开")
    decoded=0
    sampled=0
    predicted=0
    tracked=0
    ids=set()
    frame_summaries=[]
    local_boxes=[]
    try:
        with _lock:
            for frame_num in range(1,stop+1):
                ok,frame=cap.read()
                if not ok:
                    raise HTTPException(409,"CAVIAR实际解码帧不足，不能宣称模型运行成功")
                decoded+=1
                if (frame_num-1)%sample_every:
                    continue
                # Only class 0 in COCO is person; no face identification.
                raw=model.predict(source=frame,classes=[0],
                                  conf=confidence,imgsz=640,device="cpu",
                                  verbose=False)[0]
                boxes=raw.boxes
                coords=boxes.xyxy.cpu().numpy().astype(np.float32)
                scores=boxes.conf.cpu().numpy().astype(np.float32)
                detector_input=sv.Detections(
                    xyxy=coords.reshape((-1,4)),
                    confidence=scores,
                    class_id=np.zeros(len(scores),dtype=np.int32))
                result=tracker.update(detector_input)
                local_ids=result.tracker_id
                actual_ids=[] if local_ids is None else [
                    int(x) for x in local_ids if int(x)>=0]
                sampled+=1
                predicted+=len(scores)
                tracked+=len(actual_ids)
                ids.update(actual_ids)
                # Use the vendor's actual OUTPUT geometry rather than zipping
                # IDs back to potentially reordered original detector boxes.
                # The box is camera-local evidence only, never a person ID.
                for index,tid in enumerate(local_ids if local_ids is not None else []):
                    if int(tid)<0:continue
                    xyxy=result.xyxy[index].tolist()
                    x1,y1,x2,y2=[int(round(float(v))) for v in xyxy]
                    if x2<=x1 or y2<=y1:continue
                    conf_value=(float(result.confidence[index])
                                if result.confidence is not None else 0.)
                    local_boxes.append({
                        "frame_number":frame_num,"local_track_id":str(int(tid)),
                        "x":max(0,x1),"y":max(0,y1),
                        "width":x2-x1,"height":y2-y1,
                        "confidence":round(conf_value,6),
                    })
                frame_summaries.append({
                    "frame_number":frame_num,
                    "automatically_detected_person_boxes":len(scores),
                    "existing_tracker_assigned_boxes":len(actual_ids),
                    "official_manual_boxes_for_reference":gt_counts.get(frame_num,0),
                })
    finally:
        cap.release()
    return {
        "mode":"real_pretrained_person_detection_plus_existing_tracking",
        "source_id":source_id,"camera_view":record["camera_view"],
        "place_id":record["place_id"],"scenario":record["scenario"],
        "video_sha256":record["video_sha256"],"model":MODEL,
        "model_weights_sha256":weights_sha,
        "detector_confidence_threshold":confidence,
        "tracker_activation_threshold":activation_threshold,
        "tracker_assignment_coverage_of_yolo_detections":(
            round(tracked/predicted,4) if predicted else None),
        "model_license":MODEL_LICENSE,"tracker":"roboflow_trackers_bytetrack",
        "frames_decoded":decoded,"frames_inferred":sampled,
        "sampling_step":sample_every,
        "predicted_person_boxes":predicted,
        "tracker_assigned_boxes":tracked,
        "camera_local_tracker_ids":len(ids),
        "published_gt_boxes_on_inferred_frames":sum(
            gt_counts.get(x["frame_number"],0) for x in frame_summaries),
        "frame_summaries":frame_summaries,
        "local_track_boxes":local_boxes,
        "model_run_recorded":False,
        "detector_received_ground_truth_boxes":False,
        "real_pretrained_person_detector_ran":True,
        "existing_automatic_tracker_ran":True,
        "cross_camera_identity_matching_ran":False,
        "identity_accuracy_measured":False,
        "qualified_for_real_roster_or_alerts":False,
        "counted_unique_people":False,
        "clock_basis":"archival_video_frame_index",
        "limitation":"这是现成YOLO11n在官方CAVIAR MPEG解码帧上的人员检测＋现成ByteTrack本机跟踪；"
                     "抽样步长会改变跟踪的帧间时间，官方框只作独立参考。"
                     "没进行跨镜头同人识别、身份核验、精确率/召回率标定；模型适用性与AGPL授权需单独核验。",
    }


def persist_detection_run(c, result):
    """Append real inference-derived LOCAL camera evidence, never GT or P###.

    An identical pretrained model/source/sampling configuration is a stable
    immutable version. Replays are idempotent; changed model/settings have a
    distinct run key, and each run's local track IDs are independent.
    """
    payload={
        "source_id":result["source_id"],"source_sha":result["video_sha256"],
        "model":result["model"],"tracker":result["tracker"],
        "model_weights_sha256":result.get("model_weights_sha256"),
        "detector_confidence_threshold":result.get("detector_confidence_threshold"),
        # Tracker config IS part of provenance: changing activation
        # thresholds can change trajectories even for identical MPEG/model.
        "tracker_activation_threshold":result.get("tracker_activation_threshold"),
        "tracker_minimum_consecutive_frames":1,
        "pipeline_version":"yolo11n_bytetrack_gt_independent_v2",
        "frames_decoded":result["frames_decoded"],
        "sampling_step":result["sampling_step"],
    }
    run_key=sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:32]
    src=c.execute("""SELECT s.uri,s.place_id,i.video_sha256
        FROM sources s JOIN public_cctv_imports i ON i.source_id=s.id
        WHERE s.id=?""",(result["source_id"],)).fetchone()
    if (src is None or src["place_id"]!=result["place_id"] or
        src["video_sha256"]!=result["video_sha256"]):
        raise HTTPException(409,"检测期间视频版本或场所发生变化，拒绝错误归属")
    file=Path(src["uri"])
    if not file.is_file() or sha256(file.read_bytes()).hexdigest()!=src["video_sha256"]:
        raise HTTPException(409,"推理后原视频已被篡改，拒绝持久化局部目标")
    c.execute("BEGIN IMMEDIATE")
    prior=c.execute("SELECT run_key FROM model_track_runs WHERE run_key=?",
                    (run_key,)).fetchone()
    if prior:
        stored=c.execute("SELECT COUNT(*) FROM model_track_boxes WHERE run_key=?",
                         (run_key,)).fetchone()[0]
        return {"run_key":run_key,"status":"existing","stored_local_boxes":stored}
    c.execute("""INSERT INTO model_track_runs
      (run_key,source_id,video_sha256,model_name,tracker_name,params_json,
       frames_decoded,frames_inferred,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
      (run_key,result["source_id"],result["video_sha256"],result["model"],
       result["tracker"],json.dumps(payload,sort_keys=True),
       result["frames_decoded"],result["frames_inferred"],
       datetime.now(timezone.utc).isoformat(timespec="microseconds")))
    seen=set()
    rows=[]
    for box in result["local_track_boxes"]:
        tag=(box["frame_number"],box["local_track_id"])
        if tag in seen:
            raise HTTPException(409,"同一机位/帧的自动跟踪ID重复，拒绝污染证据")
        seen.add(tag)
        rows.append((run_key,box["frame_number"],box["local_track_id"],
                     box["x"],box["y"],box["width"],box["height"],
                     box["confidence"]))
    c.executemany("""INSERT INTO model_track_boxes
       (run_key,frame_number,local_track_id,x,y,width,height,confidence)
       VALUES(?,?,?,?,?,?,?,?)""",rows)
    return {"run_key":run_key,"status":"saved",
            "stored_local_boxes":len(rows)}
