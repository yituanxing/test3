"""Optional adapter to the EXISTING Roboflow ByteTrack implementation.

This module deliberately does not implement Kalman filters, assignment, or a
new tracker. It feeds CAVIAR *official manual person boxes* to
`trackers.ByteTrackTracker` and measures how stable the tracker-local IDs
are compared with camera-local published annotation IDs.

Important: using manual GT as detector input is an upper-bound TRACKING test,
not an automatic person DETECTION test and definitely not cross-camera ReID.
A separate pretrained person detector and a dataset with shared global IDs
are required before any actual cross-camera identity claims.
"""
from __future__ import annotations

from collections import defaultdict
from importlib import import_module

from fastapi import HTTPException

PROVIDER = "roboflow_trackers_bytetrack"
CONTRACT = "caviar_gt_to_existing_bytetrack_v1"


def _dependencies():
    try:
        sv = import_module("supervision")
        trackers = import_module("trackers")
        np = import_module("numpy")
        return sv, trackers, np
    except ImportError as exc:
        raise HTTPException(
            503,
            "未安装开源 ByteTrack 基线依赖；按 requirements-tracking.txt 安装 trackers，"
            "不允许改用未经测试的自研替代算法",
        ) from exc


def run_caviar_tracking(c, source_id: int, max_frames: int = 400,
                        algorithm: str = "bytetrack") -> dict:
    if algorithm not in ("bytetrack","ocsort"):
        raise ValueError("Only existing ByteTrack or OC-SORT is allowed")
    if max_frames < 1 or max_frames > 600:
        raise ValueError("max_frames must be in 1..600")
    row = c.execute("""SELECT i.scenario,i.camera_view,i.frames_available,
            i.video_sha256,s.place_id FROM public_cctv_imports i
            JOIN sources s ON s.id=i.source_id WHERE i.source_id=?""",
            (source_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "没有该摄像机的CAVIAR研究视频标注")

    sv, trackers, np = _dependencies()
    # A NEW tracker instance for EVERY camera. Never share local ID state
    # between two views; a coincidentally equal integer is NOT a person ID.
    if algorithm=="bytetrack":
        tracker=trackers.ByteTrackTracker(
            minimum_consecutive_frames=1,
            track_activation_threshold=0.5,
            high_conf_det_threshold=0.5)
    else:
        tracker=trackers.OCSORTTracker(
            minimum_consecutive_frames=1,
            high_conf_det_threshold=0.5)
    end = min(int(row["frames_available"]), max_frames)
    boxes = c.execute("""SELECT frame_number,camera_local_track_id,x,y,width,height
         FROM public_cctv_track_boxes
         WHERE source_id=? AND frame_number>=1 AND frame_number<=?
         ORDER BY frame_number,camera_local_track_id""",(source_id,end)).fetchall()
    frames = defaultdict(list)
    for b in boxes:
        frames[int(b["frame_number"])].append(dict(b))

    per_gt = defaultdict(list)
    assigned = 0
    pending = 0
    # Feed every frame, including empty frames, to keep the existing
    # tracker's state machine and occlusion buffering honest.
    for frame_number in range(1,end+1):
        frame = frames.get(frame_number,[])
        xyxy = np.asarray(
            [[b["x"],b["y"],b["x"]+b["width"],b["y"]+b["height"]]
             for b in frame],
            dtype=np.float32).reshape((-1,4))
        # Roboflow ByteTrack/OC-SORT may return detections REORDERED.
        # sv.Detections.data is indexed by their internal selection and
        # preserves the matching ground-truth row ID. Never zip output
        # tracker_id to original input order (it corrupts identity metrics).
        detections = sv.Detections(
            xyxy=xyxy,
            confidence=np.full(len(frame),0.99,dtype=np.float32),
            class_id=np.zeros(len(frame),dtype=np.int32),
            data={"input_index":np.arange(len(frame),dtype=np.int32)},
        )
        result = tracker.update(detections)
        ids = result.tracker_id
        # The vendor deliberately returns a fresh EMPTY detections object
        # without custom .data when no boxes are present. That is normal.
        original_indices = ([] if not frame else result.data.get("input_index"))
        if (ids is None or original_indices is None or
            len(ids)!=len(original_indices) or len(ids)!=len(frame)):
            raise RuntimeError("External tracker omitted or duplicated detections; cannot attribute a GT ID")
        original_indices = [int(x) for x in original_indices]
        if sorted(original_indices)!=list(range(len(frame))):
            raise RuntimeError("External tracker returned invalid original-index map")
        for original_index,raw_id in zip(original_indices,ids):
            b=frame[original_index]
            tracker_id = int(raw_id)
            if tracker_id < 0:
                pending += 1
                continue
            assigned += 1
            per_gt[str(b["camera_local_track_id"])].append((frame_number,tracker_id))

    # Camera-local truth allows evaluation of local tracking continuity ONLY.
    id_changes = 0
    fragments = 0
    seen_tracker_ids = set()
    per_track = []
    for local_id in sorted({str(b["camera_local_track_id"]) for b in boxes}):
        pairs = per_gt.get(local_id,[])
        ids = [tid for _,tid in pairs]
        distinct = set(ids)
        seen_tracker_ids.update(distinct)
        transitions = sum(a != b for a,b in zip(ids,ids[1:]))
        id_changes += transitions
        fragments += max(0,len(distinct)-1)
        per_track.append({
            "published_camera_local_id":local_id,
            "annotated_frames":sum(
                str(x["camera_local_track_id"])==local_id for x in boxes),
            "assigned_frames":len(pairs),
            "distinct_bytetrack_local_ids":len(distinct),
            "local_id_changes":transitions,
        })
    return {
        "mode":"tracking_existing_library_against_manual_ground_truth",
        "contract":CONTRACT,"tracker":(
            PROVIDER if algorithm=="bytetrack" else "roboflow_trackers_ocsort"),
        "algorithm":algorithm,
        "source_id":source_id,"place_id":row["place_id"],
        "scenario":row["scenario"],"camera_view":row["camera_view"],
        "video_sha256":row["video_sha256"],
        "frames_processed":end,
        "ground_truth_boxes":len(boxes),
        "ground_truth_local_track_count":len(per_track),
        "assigned_boxes":assigned,"unconfirmed_boxes":pending,
        "bytrack_local_track_count":len(seen_tracker_ids),
        "local_id_changes":id_changes,
        "local_gt_track_fragmentation_over_minimum":fragments,
        "assignment_coverage":round(assigned/len(boxes),4) if boxes else None,
        "per_ground_truth_track":per_track,
        "annotation_is_detector_input":True,
        "automatic_person_detection_ran":False,
        "automatic_tracking_ran":True,
        "cross_camera_matching_ran":False,
        "global_person_identity_ground_truth_available":False,
        "counts_unique_people":False,
        "feeds_real_roster_or_alerts":False,
        "clock_basis":"archival_video_frame_number",
        "limitation":"ByteTrack确实对已有公开人工框进行了自动时序关联。该GT输入"
                     "排除了检测模型误检与漏检，不能算YOLO识别精度，也不能把机位ID"
                     "当作跨摄像机同一真人的真实标签。",
    }
