"""Official EPFL six-person laboratory benchmark: real synchronized cameras + GT.

Research-use video. No images/videos archived except small annotated samples;
no name, face biometrics, or production roster/alert update. Official six
person IDs are used for *scoring only*, never provided to the matching code.
"""
from __future__ import annotations
from collections import defaultdict
from hashlib import sha256
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from scripts.verify_epfl_alternative_cameras import _track_contiguous_windows

VIDEO_BASE = "https://documents.epfl.ch/groups/c/cv/cvlab-pom-video1/www"
OFFICIAL_BASE = "https://www.epfl.ch/labs/cvlab/wp-content/uploads/2018/08"
GT_URL = OFFICIAL_BASE + "/gt_lab_6p.txt"
CAL_URL = OFFICIAL_BASE + "/calibration-6p.txt"
FRAMES = list(range(250, 2751, 125))  # Original fixed calibration baseline.
# Distinct predeclared temporal holdout; do not retune the frozen matcher on it.
HOLDOUT_FRAMES = list(range(275, 2751, 125))
ALL_SCORED_FRAMES = sorted(set(FRAMES + HOLDOUT_FRAMES))
PREVIEW_FRAMES = (500, 1750, 2500)
MAX_BYTES = 180 * 1024 * 1024
WORLD_GT_DISTANCE = 30.0
CROSS_CAMERA_DISTANCE = 25.0
OUTPUT = Path("reports/verify-epfl-six-person-cross-camera.json")
BUNDLED_CANDIDATES = Path("config/epfl6p_real_pair_candidates_v1.json")


def download_official(url: str, target: Path) -> dict:
    parsed = urllib.parse.urlsplit(url)
    video = (parsed.hostname == "documents.epfl.ch" and
             parsed.path.startswith("/groups/c/cv/cvlab-pom-video1/www/6p-c") and
             parsed.path.endswith(".avi"))
    metadata = (parsed.hostname == "www.epfl.ch" and
                url in (GT_URL, CAL_URL))
    if parsed.scheme != "https" or not (video or metadata):
        raise ValueError("Only EPFL official six-person files are allowed")
    request = urllib.request.Request(url, headers={
        "User-Agent": "ZD-EPFL-6p-multi-camera-research-verification/1.0"})
    digest = sha256()
    total = 0
    with urllib.request.urlopen(request, timeout=70) as resp:
        redirect = urllib.parse.urlsplit(resp.geturl())
        if redirect.scheme != "https" or redirect.hostname != parsed.hostname:
            raise RuntimeError("Official file redirected to unexpected host")
        with target.open("wb") as fp:
            while True:
                part = resp.read(256 * 1024)
                if not part:
                    break
                total += len(part)
                if total > (MAX_BYTES if video else 1024 * 1024):
                    raise RuntimeError("Official EPFL file exceeds CI limit")
                digest.update(part)
                fp.write(part)
    if total < 100:
        raise RuntimeError("Official EPFL file was empty")
    return {"bytes": total, "sha256": digest.hexdigest(), "url": url}


def parse_gt(data: str):
    """Rows index VIDEO 0-based frame, columns are stable EPFL GT IDs 0..5.

    -1 = not annotated; -2 = outside the tracked grid; NEVER treat -1
    as an empty scene. Only positive positions at 25-frame cadence count.
    """
    lines = [s.strip() for s in data.splitlines() if s.strip()]
    if lines[0] != "1":
        raise ValueError("Unexpected official ground truth format version")
    length, persons, gw, gh, step, first, last = map(int, lines[1].split())
    if (persons, gw, gh, step, first, last) != (6, 56, 56, 25, 0, 2950):
        raise ValueError("Unexpected ground truth metadata")
    if len(lines) != length + 2:
        raise ValueError("Ground truth frame count mismatch")
    labels = {}
    for index, line in enumerate(lines[2:]):
        vals = [int(i) for i in line.split()]
        if len(vals) != persons or any(v < -2 or v >= gw * gh for v in vals):
            raise ValueError(f"Bad ground truth row {index}")
        if index % step == 0:
            labels[index] = {
                person_id: ((v % gw + 0.5) * 358.0 / gw,
                            (v // gw + 0.5) * 360.0 / gh)
                for person_id, v in enumerate(vals) if v >= 0
            }
    return labels, {
        "raw_frames": length,
        "people": persons,
        "grid": [gw, gh],
        "annotation_step": step,
        "annotated_frames": len(labels),
        "frame_0_based": True,
        "negative_1_is_missing_annotation": True,
        "negative_2_is_outside_grid": True,
    }


def parse_calibration(source: str):
    """EPFL H is IMAGE --> COMMON GROUND plane, not its inverse."""
    matrices = {}
    for camera in (0, 1):
        section = re.search(
            r"# Camera " + str(camera) +
            r"\s*#+\s*# Ground plane homography\s*"
            r"([^#]+)", source, re.IGNORECASE)
        if not section:
            raise ValueError(f"Official calibration camera {camera} absent")
        rows = [r.split() for r in section.group(1).strip().splitlines()
                if r.strip()]
        if len(rows) != 3 or any(len(row) != 3 for row in rows):
            raise ValueError("Malformed homography matrix")
        h = np.asarray([[float(v) for v in row] for row in rows])
        if abs(np.linalg.det(h)) < 1e-8:
            raise ValueError("Singular official EPFL homography")
        matrices[camera] = h
    return matrices


def project_foot(h, xyxy):
    x1, y1, x2, y2 = map(float, xyxy)
    point = h @ np.array([(x1 + x2) / 2, y2, 1.0])
    if abs(point[2]) < 1e-8:
        return None
    x, y = point[:2] / point[2]
    if not np.isfinite((x, y)).all():
        return None
    return (float(x), float(y))


def assign_points(left, right, maximum):
    """Gated 1-1 global min cost matching, without any GT labels as input.

    left/right are lists of world XY points. Returns (i,j,distance).
    """
    from scipy.optimize import linear_sum_assignment
    if not left or not right:
        return []
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    dist = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    # Augment with dummy nodes; prohibit unmatched-pair distances above
    # the user-independent geometric gate without forcing invalid matches.
    size = len(left) + len(right)
    cost = np.full((size, size), maximum + 1.0)
    cost[:len(left), :len(right)] = np.where(
        dist <= maximum, dist, 3 * maximum + 3.0)
    cost[len(left):, len(right):] = 0
    rows, cols = linear_sum_assignment(cost)
    return [
        (int(i), int(j), round(float(dist[i, j]), 3))
        for i, j in zip(rows, cols)
        if i < len(left) and j < len(right) and dist[i, j] <= maximum
    ]


def real_detections(video_path, camera, homography, model):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError("Official six-person video cannot be decoded")
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    selected = set(ALL_SCORED_FRAMES)
    found = {}
    previews = {}
    index = 0
    try:
        while index <= max(selected):
            ok, image = cap.read()
            if not ok:
                break
            if index in selected:
                if index in PREVIEW_FRAMES:
                    # Research stills only, never the original video in CI artifact.
                    previews[index] = image.copy()
                boxes = model.predict(
                    source=image, classes=[0], conf=.25, imgsz=640,
                    device="cpu", verbose=False)[0].boxes
                xyxy = boxes.xyxy.cpu().numpy()
                confidence = boxes.conf.cpu().numpy()
                observations = []
                for box, score in zip(xyxy, confidence):
                    world = project_foot(homography, box)
                    if world is None:
                        continue
                    observations.append({
                        "xyxy": [round(float(x), 1) for x in box],
                        "confidence": round(float(score), 4),
                        "world_xy": [round(v, 3) for v in world],
                    })
                found[index] = observations
            index += 1
    finally:
        cap.release()
    if len(found) != len(ALL_SCORED_FRAMES):
        raise RuntimeError(
            f"Camera {camera}: expected {len(ALL_SCORED_FRAMES)} official labeled "
            f"frames, decoded only {len(found)}, last decoded {index}")
    return found, previews, {
        "decoded_up_to_0_based_frame": index - 1,
        "fps": round(actual_fps, 3),
        "sampled_labeled_frames": len(found),
        "total_model_person_detections": sum(map(len, found.values()))
    }


def audit_pair(observations, truth):
    """Official GT used only after prediction and geometric matching."""
    cam0, cam1 = observations
    links = assign_points(
        [x["world_xy"] for x in cam0],
        [x["world_xy"] for x in cam1],
        CROSS_CAMERA_DISTANCE)
    def to_gt(obs):
        labels = sorted(truth)
        matches = assign_points(
            [x["world_xy"] for x in obs],
            [truth[k] for k in labels],
            WORLD_GT_DISTANCE)
        return {i: labels[j] for i, j, d in matches}
    label0, label1 = to_gt(cam0), to_gt(cam1)
    gt_in_both = set(label0.values()) & set(label1.values())
    valid = 0
    mismatched = 0
    unknown = 0
    found_ids = set()
    for i, j, dist in links:
        if i not in label0 or j not in label1:
            unknown += 1
        elif label0[i] == label1[j]:
            valid += 1
            found_ids.add(label0[i])
        else:
            mismatched += 1
    return {
        "camera_0_predicted_person_boxes": len(cam0),
        "camera_1_predicted_person_boxes": len(cam1),
        "gt_people_in_grid": len(truth),
        "independently_gt_matched_camera_0_boxes": len(label0),
        "independently_gt_matched_camera_1_boxes": len(label1),
        "cross_camera_candidates": len(links),
        "verified_same_gt_person_matches": valid,
        "verified_different_gt_person_matches": mismatched,
        "unscorable_pairs_no_two_sided_gt": unknown,
        "gt_persons_independently_detected_in_both_views": len(gt_in_both),
        "eligible_gt_persons_covered_by_true_pair": len(found_ids),
        "association_world_distance_max": CROSS_CAMERA_DISTANCE,
        "annotation_scoring_distance_max": WORLD_GT_DISTANCE,
        "candidate_evidence": [{
            "cam0_model_box_index": i, "cam1_model_box_index": j,
            "calibration_distance": dist,
            "audit_gt_cam0": label0.get(i),
            "audit_gt_cam1": label1.get(j),
            # Detector output in common ground coordinates, NEVER official GT.
            "cam0_ground_xy": cam0[i]["world_xy"],
            "cam1_ground_xy": cam1[j]["world_xy"],
        } for i,j,dist in links],
    }


def _sum_audits(rows):
    total = defaultdict(int)
    for audit in rows:
        for key in ("cross_camera_candidates", "verified_same_gt_person_matches",
                    "verified_different_gt_person_matches",
                    "unscorable_pairs_no_two_sided_gt",
                    "gt_persons_independently_detected_in_both_views",
                    "eligible_gt_persons_covered_by_true_pair"):
            total[key] += audit[key]
    positive = total["verified_same_gt_person_matches"]
    erroneous = total["verified_different_gt_person_matches"]
    scorable = positive + erroneous
    eligible = total["gt_persons_independently_detected_in_both_views"]
    return {
        **total,
        "scorable_pair_precision": round(positive / scorable, 4) if scorable else None,
        "detected_both_sides_pair_recall": (
            round(total["eligible_gt_persons_covered_by_true_pair"] / eligible, 4)
            if eligible else None),
        "scored_frame_count": len(rows),
        "not_full_person_recall": True,
        "not_a_unique_people_measure": True,
        "unmatched_GT_no_camera_visibility_labels": True,
        "verification_scope": "anonymous per-frame association, not named identity",
        "real_identity_global_person_roster_verified": False,
        "automatic_real_person_alerts_verified": False,
        "go_no_go": ("REVIEW_CANDIDATES_ONLY" if scorable > 0
                     else "NO_SCORABLE_CROSS_CAMERA_PAIRS"),
    }


def audit_temporal_fragments(evidence):
    """Score separate cross-time links against GT AFTER all matching.

    The tracker sees detector positions only. This evaluation never supplies
    official person IDs to temporal association nor to the packaged bundle.
    """
    from app.real_activity import track_fragments
    records = []
    labels = {}
    for row in sorted(evidence["frame_audits"] + evidence["holdout_frame_audits"],
                      key=lambda r: r["frame_0_based"]):
        frame = row["frame_0_based"]
        for index, pair in enumerate(row["candidate_evidence"]):
            key = f"{frame}:{index}"
            a, b = pair["cam0_ground_xy"], pair["cam1_ground_xy"]
            records.append({
                "candidate_key":key, "frame_0_based":frame,
                "cam0_ground_x":a[0], "cam0_ground_y":a[1],
                "cam1_ground_x":b[0], "cam1_ground_y":b[1]
            })
            # Scoring only; these NEVER enter track_fragments().
            labels[key] = (pair.get("audit_gt_cam0"), pair.get("audit_gt_cam1"))
    fragments = track_fragments(records)
    count = 0
    mixed = 0
    unscorable = 0
    scorable = 0
    for fragment in fragments:
        if len(fragment["observations"]) < 2:
            continue
        count += 1
        ids = [labels[o["candidate_key"]] for o in fragment["observations"]]
        if any(left is None or right is None or left != right for left,right in ids):
            unscorable += 1
            continue
        scorable += 1
        if len({left for left, right in ids}) > 1:
            mixed += 1
    return {
        "candidate_records":len(records),
        "temporal_fragments":len(fragments),
        "multi_instant_fragments":count,
        "scorable_multi_instant_fragments":scorable,
        "unscorable_multi_instant_fragments":unscorable,
        "wrongly_merged_gt_person_fragments":mixed,
        "gt_only_used_for_post_link_scoring":True,
        "not_cross_site_identity":True,
        "not_unique_people":True
    }


def _paired_preview(frame, image_by_camera, predictions, audit):
    """Two original camera frames with YOLO boxes and candidate lines.

    Never draws official GT identity labels in user-facing evidence.
    """
    left = image_by_camera[0][frame].copy()
    right = image_by_camera[1][frame].copy()
    h = max(left.shape[0], right.shape[0])
    w0 = left.shape[1]
    target = np.zeros((h + 30, w0 + right.shape[1], 3), dtype=np.uint8)
    target[30:30 + left.shape[0], :w0] = left
    target[30:30 + right.shape[0], w0:] = right
    for cam, offset in ((0, 0), (1, w0)):
        for i, item in enumerate(predictions[cam][frame]):
            x1, y1, x2, y2 = [int(round(v)) for v in item["xyxy"]]
            cv2.rectangle(target, (offset+x1, y1+30),
                          (offset+x2, y2+30), (255, 160, 25), 2)
            cv2.putText(target, str(i), (offset+x1, max(41, y1+23)),
                        cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 160, 25), 1)
    for link in audit["candidate_evidence"]:
        a = predictions[0][frame][link["cam0_model_box_index"]]["xyxy"]
        b = predictions[1][frame][link["cam1_model_box_index"]]["xyxy"]
        p0 = (int((a[0]+a[2])/2), int((a[1]+a[3])/2)+30)
        p1 = (w0+int((b[0]+b[2])/2), int((b[1]+b[3])/2)+30)
        cv2.line(target, p0, p1, (35, 240, 100), 1)
    cv2.putText(target, f"EPFL SIX PERSON | frame {frame} | "
                "YOLO + geometry candidate links, NO identity GT as input",
                (5, 19), cv2.FONT_HERSHEY_SIMPLEX, .38,
                (255, 255, 255), 1)
    output = Path(f"reports/epfl6p-paired-frame-{frame}.jpg")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), target, [cv2.IMWRITE_JPEG_QUALITY, 85]):
        raise RuntimeError("Failed to preserve research candidate screenshot")
    return str(output)

def _write_candidate_bundle(evidence):
    """Bundle only detector-derived candidate geometry, never EPFL ground truth.

    Regenerated as part of the ACTUAL official-video owner job, then shipped
    with the local demo ZIP. Does not contain licensed image pixels, 6p GT IDs,
    faces, names, or production alerts; not a general ReID benchmark.
    """
    bundle = {
        "schema_version": "epfl6p_anonymous_candidate_bundle_v1",
        "dataset": evidence["dataset"],
        "selected_cameras": [0, 1],
        "model": evidence["model"],
        "matching_algorithm": evidence["matching_algorithm"],
        "matching_gt_is_never_input": True,
        "production_real_roster_or_alerts": False,
        "temporal_fragment_verified": evidence["temporal_fragment_audit"]["temporal_fragment_verified"],
        "temporal_delivery_decision": evidence["temporal_fragment_audit"]["delivery_decision"],
        "license": "EPFL research only; no original AVI or ground truth included",
        "generated_from": "real_video_owner_ci_detector_result",
        "source_ci_run_id": os.environ.get("GITHUB_RUN_ID"),
        "per_camera": {str(i): {
            "sha256": evidence["per_camera"][str(i)]["sha256"],
            "fps": evidence["per_camera"][str(i)]["fps"],
        } for i in (0, 1)},
    }
    for key in ("frame_audits", "holdout_frame_audits"):
        bundle[key] = [{
            "frame_0_based": row["frame_0_based"],
            "cross_camera_candidates": row["cross_camera_candidates"],
            "candidate_evidence": [{
                "cam0_model_box_index": p["cam0_model_box_index"],
                "cam1_model_box_index": p["cam1_model_box_index"],
                "calibration_distance": p["calibration_distance"],
                "cam0_ground_xy": p["cam0_ground_xy"],
                "cam1_ground_xy": p["cam1_ground_xy"],
            } for p in row["candidate_evidence"]],
        } for row in evidence[key]]
    raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    if "audit_gt_" in raw or '"ground_truth"' in raw:
        raise AssertionError("Never package official 6p GT person IDs")
    BUNDLED_CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    BUNDLED_CANDIDATES.write_text(raw + "\n", encoding="utf-8")
    return {"bundled_candidates": sum(
        len(r["candidate_evidence"])
        for key in ("frame_audits", "holdout_frame_audits")
        for r in bundle[key]
    ), "manifest_sha256": sha256(raw.encode()).hexdigest()}


def main():
    from ultralytics import YOLO
    model = YOLO("yolo11n.pt")
    with tempfile.TemporaryDirectory(prefix="epfl-6p-real-") as td:
        tmp = Path(td)
        evidence = {
            "license": "CVLab-EPFL copyrighted; research use only",
            "official_dataset": "https://www.epfl.ch/labs/cvlab/data/data-pom-index-php/",
            "dataset": "laboratory_six_person_four_synced_views",
            "selected_cameras": [0,1],
            "model": "yolo11n.pt",
            "confidence": 0.25,
            "input_size": 640,
            "annotation_type": "global ground-plane grid identity at 25 frame intervals",
            "matching_algorithm": "fixed-camera calibrated-ground-plane Hungarian gate",
            "matching_gt_is_never_input": True,
            "production_real_roster_or_alerts": False,
            "metadata": {},
            "per_camera": {},
            "frame_audits": [],
            "holdout_frame_audits": [],
            "holdout_plan": {
                "original_frames": FRAMES,
                "fixed_untuned_holdout_frames": HOLDOUT_FRAMES,
                "same_original_videos_model_and_calibration": True,
            },
            "candidate_preview_jpegs": [],
        }
        for name, url in (("gt", GT_URL), ("calibration", CAL_URL)):
            target = tmp / (name + ".txt")
            evidence["metadata"][name] = download_official(url, target)
        gt, info = parse_gt((tmp / "gt.txt").read_text(encoding="utf-8"))
        matrices = parse_calibration(
            (tmp / "calibration.txt").read_text(encoding="utf-8"))
        evidence["ground_truth"] = info
        observations = {}
        previews = {}
        for camera in (0, 1):
            p = tmp / f"6p-c{camera}.avi"
            downloaded = download_official(
                f"{VIDEO_BASE}/6p-c{camera}.avi", p)
            predictions, preview, summary = real_detections(
                p, camera, matrices[camera], model)
            previews[camera] = preview
            # Reuse the previous REAL consecutive-frame ByteTrack test.
            local_tracking = _track_contiguous_windows(p, camera, model)
            evidence["per_camera"][str(camera)] = {
                **downloaded, **summary, "local_tracking": local_tracking,
            }
            observations[camera] = predictions
        for key,frames in (
            ("frame_audits", FRAMES),
            ("holdout_frame_audits", HOLDOUT_FRAMES),
        ):
            for frame in frames:
                audit = audit_pair(
                    (observations[0][frame], observations[1][frame]),
                    gt[frame])
                evidence[key].append({"frame_0_based": frame, **audit})
        evidence["summary"] = _sum_audits(evidence["frame_audits"])
        evidence["holdout_summary"] = _sum_audits(evidence["holdout_frame_audits"])
        evidence["temporal_fragment_audit"] = audit_temporal_fragments(evidence)
        # Important: the older synchronous CAMERA-TO-CAMERA frame pair gate
        # remains valid even when an experimental CROSS-TIME tracker switches
        # people. In that case DISABLE cross-time IDs in the shipped activity
        # viewer; keep the 119 real paired observations and their true frame
        # clocks instead of falsely claiming long-range identity.
        temporal = evidence["temporal_fragment_audit"]
        temporal["temporal_fragment_verified"] = (
            temporal["scorable_multi_instant_fragments"] > 0
            and temporal["wrongly_merged_gt_person_fragments"] == 0
            and temporal["unscorable_multi_instant_fragments"] == 0)
        temporal["delivery_decision"] = (
            "VERIFIED_ANONYMOUS_CROSS_TIME_FRAGMENTS"
            if temporal["temporal_fragment_verified"]
            else "PER_FRAME_DUAL_CAMERA_MATCHES_ONLY_NO_CROSS_TIME_ID")
        for key in ("summary", "holdout_summary"):
            report = evidence[key]
            if report["verified_different_gt_person_matches"] != 0 or (
                    report["verified_same_gt_person_matches"] <= 0):
                raise RuntimeError("Even the actual per-frame dual-camera real match failed")
        for frame in PREVIEW_FRAMES:
            audit = next(x for x in evidence["frame_audits"]
                         if x["frame_0_based"] == frame)
            evidence["candidate_preview_jpegs"].append(
                _paired_preview(frame, previews, observations, audit))
    OUTPUT.parent.mkdir(exist_ok=True, parents=True)
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    bundled = _write_candidate_bundle(evidence)
    print(json.dumps({
        "dataset": evidence["dataset"], "cameras": evidence["selected_cameras"],
        "GT": info, "summary": evidence["summary"],
        "holdout_summary": evidence["holdout_summary"],
        "temporal_fragment_audit": evidence["temporal_fragment_audit"],
        "packaged_research_candidates": bundled,
        "camera_detection_counts": {
            camera: evidence["per_camera"][str(camera)]["total_model_person_detections"]
            for camera in (0, 1)},
        "warning": "GT used exclusively for scoring. No operational identity/alerts.",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
