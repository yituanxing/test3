"""Real, recorded-video anonymous activity from EPFL calibrated camera pairs.

Never joins unrelated physical places, Frigate live records, real-world people,
or P### records. Only pair coordinates extracted from TWO ACTUAL original AVIs
may be linked in this bounded lab scene. Official global identity labels are
strictly scoring-only and are not stored or accepted by this reader.
"""
from __future__ import annotations

from collections import defaultdict
from math import hypot, isfinite
from fastapi import APIRouter, Request

from app import access
from app import epfl_review

router = APIRouter()
_db = None

# Dataset-scoped EPFL research parameters. No real identity or general ReID.
# Link ONLY immediate 25-frame neighbors (~1s) with mutual-nearest position,
# 30 grid-unit displacement and >=35-unit separation to the next alternative.
TEMPORAL_GAP_FRAMES = 25
TEMPORAL_MAX_DISTANCE = 30.0
TEMPORAL_ISOLATION_MARGIN = 35.0


def _center(row):
    keys = ("cam0_ground_x", "cam0_ground_y",
            "cam1_ground_x", "cam1_ground_y")
    points = [row.get(k) for k in keys]
    if any(x is None or not isfinite(float(x)) for x in points):
        return None
    x0, y0, x1, y1 = (float(x) for x in points)
    return (round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3))


def track_fragments(rows, allow_temporal=True):
    """Bounded tracklets across the two *real* EPFL synchronized views.

    Uses detector-derived ground points only, never official person identity.
    Refuses to connect when either side has another reasonably close option.
    Only the exact adjacent 25-frame pair is eligible; missed detections
    terminate the fragment rather than guessing identity through a long gap.
    """
    ordered = sorted((dict(r) for r in rows),
                     key=lambda r: (r["frame_0_based"], r["candidate_key"]))
    frames = sorted({r["frame_0_based"] for r in ordered})
    fragments = []
    last_frame = {}
    for frame in frames:
        current = [r for r in ordered if r["frame_0_based"] == frame]
        for row in current:
            xy = _center(row)
            row["ground_xy"] = list(xy) if xy is not None else None

        prev_tracks = last_frame.get(frame - TEMPORAL_GAP_FRAMES, [])
        positions = [(idx, fragments[ti]["ground_xy"])
                     for idx, ti in enumerate(prev_tracks)
                     if fragments[ti]["ground_xy"] is not None]
        edges = []
        if allow_temporal:
            for curidx, cur in enumerate(current):
                if cur["ground_xy"] is None:
                    continue
                for prev_idx, xy in positions:
                    distance = hypot(cur["ground_xy"][0] - xy[0],
                                     cur["ground_xy"][1] - xy[1])
                    edges.append((distance, curidx, prev_idx))
        picked = []
        for distance, ci, pi in sorted(edges):
            if distance > TEMPORAL_MAX_DISTANCE:
                continue
            # Compare ALL alternatives (not only other candidates inside the
            # distance gate), otherwise a nearby second person is ignored.
            others_cur = [d for d, i, j in edges if i == ci and j != pi]
            others_prev = [d for d, i, j in edges if j == pi and i != ci]
            if (others_cur and min(others_cur) - distance <
                    TEMPORAL_ISOLATION_MARGIN):
                continue
            if (others_prev and min(others_prev) - distance <
                    TEMPORAL_ISOLATION_MARGIN):
                continue
            picked.append((distance, ci, pi))

        by_current = {}
        used_previous = set()
        for distance, ci, pi in picked:
            if ci in by_current or pi in used_previous:
                continue
            by_current[ci] = prev_tracks[pi]
            used_previous.add(pi)

        current_fragments = []
        for idx, row in enumerate(current):
            if idx in by_current:
                trackid = by_current[idx]
                item = fragments[trackid]
                item["end_frame"] = frame
                item["ground_xy"] = row["ground_xy"]
                item["observations"].append(row)
            else:
                trackid = len(fragments)
                fragments.append({
                    "fragment_id": "V%03d" % (trackid + 1),
                    "start_frame": frame, "end_frame": frame,
                    "ground_xy": row["ground_xy"],
                    "observations": [row],
                })
            current_fragments.append(trackid)
        last_frame[frame] = current_fragments
    return fragments


def activity_summary(rows, fps=None, temporal_verified=False):
    fragments = track_fragments(rows, allow_temporal=temporal_verified)
    timeline = []
    for frame in sorted({r["frame_0_based"] for r in rows}):
        n = sum(1 for r in rows if r["frame_0_based"] == frame)
        timeline.append({"frame_0_based": frame,
                         "video_second": round(frame / fps, 3) if fps else None,
                         "paired_person_observations": n})
    details = []
    for fragment in fragments:
        items = fragment["observations"]
        details.append({
            "activity_id": fragment["fragment_id"],
            "first_video_frame": fragment["start_frame"],
            "last_video_frame": fragment["end_frame"],
            "first_video_second": (
                round(fragment["start_frame"] / fps, 3) if fps else None),
            "last_video_second": (
                round(fragment["end_frame"] / fps, 3) if fps else None),
            "synchronized_pair_observations": len(items),
            "camera_count": 2,
            "camera_names": ["6p-c0.avi", "6p-c1.avi"],
            "fragment_only_not_verified_identity": True,
            "cross_frame_linked": len(items) > 1,
            "samples": [{
                "candidate_key": o["candidate_key"],
                "frame_0_based": o["frame_0_based"],
                "video_second": (
                    round(o["frame_0_based"] / fps, 3) if fps else None),
                "cam0_box_index": o["cam0_box_index"],
                "cam1_box_index": o["cam1_box_index"],
                "calibration_distance": o["ground_distance"],
                "common_ground_xy": o["ground_xy"],
                "review_verdict": o.get("verdict") or "unreviewed"
            } for o in items]
        })
    return {
        "matched_pair_observations": len(rows),
        "sampled_video_instants": len(timeline),
        "anonymous_activity_fragments": len(details),
        "multi_instant_fragments": sum(
            x["cross_frame_linked"] for x in details),
        "timeline": timeline,
        "activities": sorted(details, key=lambda v: (
            -v["synchronized_pair_observations"], v["activity_id"])),
    }


def register(app, db):
    global _db
    _db = db
    app.include_router(router)


@router.get("/api/real/activity")
def real_activity(request: Request):
    # EPFL laboratory is a separate scientific site, outside an operator's
    # jurisdiction. Only a state-level research admin can view it.
    epfl_review._admin(request)
    with _db() as db:
        report = db.execute("""SELECT report_digest, origin, imported_at,
           video_sha0, video_sha1, video_fps, temporal_fragment_verified
           FROM epfl_candidate_reports
           WHERE origin='ci_generated_research_bundle'
           ORDER BY imported_at DESC,rowid DESC LIMIT 1""").fetchone()
        if not report:
            return {"mode": "recorded_real_video_activity", "ready": False,
                    "matched_pair_observations": 0,
                    "sampled_video_instants": 0,
                    "anonymous_activity_fragments": 0,
                    "activities": [], "timeline": [],
                    "confirmed_natural_person_identity": False,
                    "real_cross_site_visits": None,
                    "real_identity_alerts": [],
                    "note": "尚未导入真正从双机原片推理产生的EPFL证据；不生成演示人员。"}
        rows = db.execute("""SELECT p.candidate_key,p.frame_0_based,
           p.cam0_box_index,p.cam1_box_index,p.ground_distance,
           p.cam0_ground_x,p.cam0_ground_y,p.cam1_ground_x,p.cam1_ground_y,
           review.verdict
           FROM epfl_pair_candidates p
           LEFT JOIN epfl_pair_reviews review ON review.id=(
              SELECT MAX(id) FROM epfl_pair_reviews
              WHERE candidate_key=p.candidate_key)
           WHERE p.report_digest=? ORDER BY p.frame_0_based,
             p.cam0_box_index,p.cam1_box_index""",
           (report["report_digest"],)).fetchall()
        # Actual rejected matches never enter the personnel activity reader.
        active = [dict(r) for r in rows if r["verdict"] != "false_match"]
        fps = report["video_fps"]
        temporal_verified = bool(report["temporal_fragment_verified"])
        result = activity_summary(active, fps if fps and fps > 0 else None,
                                  temporal_verified=temporal_verified)
        return {
            "mode": "recorded_real_video_activity", "ready": True,
            "site": "EPFL 6-person research laboratory (one real physical site)",
            "source_kind": "recorded_official_public_two_camera_video",
            "video_clock": "source_frame_number_not_wall_clock",
            "cameras": ["6p-c0.avi", "6p-c1.avi"],
            "camera_sha256": [report["video_sha0"], report["video_sha1"]],
            "report_digest": report["report_digest"],
            "manifest_origin": report["origin"],
            "video_fps": fps,
            "cross_time_temporal_linkage_verified": temporal_verified,
            "cross_time_status": ("independently_scored_official_GT"
                                  if temporal_verified
                                  else "DISABLED_independent_GT_found_unreliable_links"),
            "excluded_manual_false_matches": len(rows) - len(active),
            "confirmed_natural_person_identity": False,
            "verified_unique_people": None,
            "real_cross_site_visits": None,
            "real_identity_alerts": [],
            "scope": "same-site per-frame verified dual-camera anonymous activity; cross-time tracklets only if separately certified",
            "note": "真实公开视频经YOLO及几何标定生成；若跨时刻关联未通过独立真值审核，V编号严格仅代表单帧双机配对，不是实名、跨场所身份或去重人数。",
            **result,
        }
