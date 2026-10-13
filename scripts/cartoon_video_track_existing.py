"""Review actual DINOv2 auto-box predictions with OFF-THE-SHELF ByteTrack.

NO new detector/tracker training or manual role boxes. ByteTrack is only a
within-video temporal association, NOT cross-camera identification. Original
DINOv2 scores and raw labels never change. Track consensus is ADDITIONAL
evidence if a given tracked rabbit has >=2 independent strong matches.

Input: scripts.cartoon_video_recognize generated original result.json + frames.
Output: independent tracked_result.json with time, box, track ID, raw
scores, and explicitly-labeled recovered candidate frames.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

from scripts.cartoon_video_recognize import OUT_DEFAULT


def iou(left, right) -> float:
    ax, ay, bx, by = left
    cx, cy, dx, dy = right
    overlap = max(0, min(bx, dx) - max(ax, cx)) * max(
        0, min(by, dy) - max(ay, cy))
    area = max(0, bx-ax) * max(0, by-ay) + max(0, dx-cx) * max(0, dy-cy)
    return overlap / (area - overlap) if area > overlap else 0.0


def analyze(original: dict, *, frame_rate: float = 1.0,
            minimum_iou: float = 0.25) -> dict:
    """Group existing actual detections using Roboflow's prebuilt ByteTrack."""
    tracker = sv.ByteTrack(
        track_activation_threshold=0.25,
        lost_track_buffer=60,
        minimum_matching_threshold=0.8,
        frame_rate=frame_rate,
        minimum_consecutive_frames=1,
    )
    frames = []
    tracks = defaultdict(list)
    for frame in original["frames"]:
        candidates = frame["proposals"]
        # ByteTrack needs the existing raw detector confidence and xyxy box.
        xyxy = np.asarray(
            [p["box_xyxy"] for p in candidates], dtype=np.float32
        ).reshape((-1, 4))
        conf = np.asarray(
            [p["detection_score"] for p in candidates], dtype=np.float32)
        detections = sv.Detections(
            xyxy=xyxy, confidence=conf,
            class_id=np.zeros((len(candidates),), dtype=np.int32),
        )
        current = tracker.update_with_detections(detections)
        # STrack output is Kalman-predicted geometry, so recover original
        # detector proposals via *one-to-one IoU* rather than assuming rows
        # retain the same order.
        potential = []
        for current_idx in range(len(current)):
            if current.tracker_id is None:
                continue
            track_id = int(current.tracker_id[current_idx])
            if track_id < 0:
                continue
            for original_idx, prop in enumerate(candidates):
                overlap = iou(current.xyxy[current_idx], prop["box_xyxy"])
                if overlap >= minimum_iou:
                    potential.append((overlap, track_id, original_idx))
        potential.sort(reverse=True)
        claimed_track, claimed_props = set(), set()
        assignment = {}
        for _, tid, idx in potential:
            if tid in claimed_track or idx in claimed_props:
                continue
            assignment[idx] = tid
            claimed_track.add(tid)
            claimed_props.add(idx)
        observations = []
        for index, proposal in enumerate(candidates):
            event = {
                "source_time_second": frame["second"],
                "source_box_xyxy": proposal["box_xyxy"],
                "detector_confidence": proposal["detection_score"],
                "dino_cosine_similarity": proposal["cosine_similarity"],
                "raw_verdict": proposal["verdict"],
                "track_id": assignment.get(index),
            }
            observations.append(event)
            if event["track_id"] is not None:
                tracks[event["track_id"]].append(event)
        frames.append({"second": frame["second"], "observations": observations})

    status = {}
    for track_id, observations in tracks.items():
        strong_times = sorted({
            p["source_time_second"] for p in observations
            if p["raw_verdict"] == "buck_candidate"
        })
        status[track_id] = {
            "track_id": track_id,
            "strong_independent_timestamps": strong_times,
            "observations": len(observations),
            "temporal_buck_support": len(strong_times) >= 2,
        }
    promoted = []
    for frame in frames:
        for proposal in frame["observations"]:
            track_id = proposal["track_id"]
            confirmed = (track_id is not None and
                         status[track_id]["temporal_buck_support"])
            if confirmed and proposal["raw_verdict"] == "not_confirmed":
                proposal["temporal_verdict"] = "buck_by_track_evidence"
                promoted.append(proposal)
            else:
                proposal["temporal_verdict"] = proposal["raw_verdict"]
    raw_frames = {
        f["second"] for f in frames
        if any(p["raw_verdict"] == "buck_candidate" for p in f["observations"])
    }
    temporal_frames = {
        f["second"] for f in frames
        if any(p["temporal_verdict"] in {"buck_candidate", "buck_by_track_evidence"}
               for p in f["observations"])
    }
    return {
        "source": "unchanged Grounding DINO + DINOv2 output",
        "tracker": "Roboflow supervision.ByteTrack, pretrained-free algorithm",
        "bbox_source": "existing pretrained Grounding DINO outputs",
        "does_not_establish_real_world_identity": True,
        "all_raw_cosine_scores_preserved": True,
        "frames": frames,
        "track_evidence": list(status.values()),
        "raw_matching_timestamps": sorted(raw_frames),
        "temporally_supported_timestamps": sorted(temporal_frames),
        "additional_temporally_supported_timestamps": sorted(
            temporal_frames - raw_frames),
        "weak_dino_observations_recovered": len(promoted),
        "unmatched_observations": sum(
            p["track_id"] is None for f in frames for p in f["observations"]),
        "raw_track_id_coverage": sum(
            p["track_id"] is not None for f in frames for p in f["observations"]),
    }


def render_evidence(result: dict, source: Path):
    folder = source / "tracked_frames"
    folder.mkdir(exist_ok=True)
    for frame in result["frames"]:
        name = f't{frame["second"]:.2f}.jpg'
        # Original frames contain green strong and yellow weak markers.
        original = cv2.imread(str(source / "frames" / name))
        if original is None:
            continue
        for event in frame["observations"]:
            if event["temporal_verdict"] != "buck_by_track_evidence":
                continue
            x0, y0, x1, y1 = event["source_box_xyxy"]
            cv2.rectangle(original, (x0, y0), (x1, y1), (255, 50, 220), 3)
            cv2.putText(
                original, f'TRACK SUPPORT #{event["track_id"]}',
                (x0, min(original.shape[0] - 8, y1 + 16)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 50, 220), 1)
        cv2.imwrite(str(folder / name), original)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Use existing ByteTrack to group genuine video detections")
    parser.add_argument("--source", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args(argv)
    data = json.loads((args.source / "result.json").read_text(encoding="utf-8"))
    result = analyze(data)
    (args.source / "tracked_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    render_evidence(result, args.source)
    print(json.dumps({
        "tracker": result["tracker"],
        "raw_matching_timestamps": result["raw_matching_timestamps"],
        "additional_temporally_supported_timestamps": result["additional_temporally_supported_timestamps"],
        "weak_dino_observations_recovered": result["weak_dino_observations_recovered"],
        "unmatched_observations": result["unmatched_observations"],
        "tracks": result["track_evidence"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
