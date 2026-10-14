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

# Fixed, conservative association gates in EPFL common ground-grid units.
# Uncertain/ambiguous movement causes a NEW fragment, not an identity merge.
MAX_GAP_FRAMES = 150
MAX_MOVEMENT_PER_125_FRAMES = 40.0
AMBIGUITY_MARGIN = 8.0


def _center(row):
    keys = ("cam0_ground_x", "cam0_ground_y",
            "cam1_ground_x", "cam1_ground_y")
    points = [row.get(k) for k in keys]
    if any(x is None or not isfinite(float(x)) for x in points):
        return None
    x0, y0, x1, y1 = (float(x) for x in points)
    return (round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3))


def track_fragments(rows, allow_temporal=True):
    """Associate different TIMESTAMPS without GT. No cross-site ReID.

    A pair already refers to the same time in two calibrated cameras.
    The temporal association is separate and only accepted on unambiguous
    mutual-nearest ground plane positions. Return abstention by fragmentation.
    """
    ordered = sorted((dict(x) for x in rows),
                     key=lambda x: (x["frame_0_based"], x["candidate_key"]))
    fragments = []
    for frame in sorted({r["frame_0_based"] for r in ordered}):
        current = [r for r in ordered if r["frame_0_based"] == frame]
        eligible = []
        for index, row in enumerate(current):
            xy = _center(row)
            row["ground_xy"] = list(xy) if xy else None
            if xy is None or not allow_temporal:
                continue
            for fidx, track in enumerate(fragments):
                gap = frame - track["end_frame"]
                if not 0 < gap <= MAX_GAP_FRAMES:
                    continue
                dist = hypot(xy[0] - track["ground_xy"][0],
                             xy[1] - track["ground_xy"][1])
                gate = MAX_MOVEMENT_PER_125_FRAMES * gap / 125.0
                if dist <= gate:
                    eligible.append((dist, index, fidx))
        # A match is accepted only if mutual nearest and both its alternative
        # candidates are farther by the fixed ambiguity margin.
        picked = []
        for dist, idx, fidx in sorted(eligible):
            competitor_row = [d for d, i, f in eligible if i == idx and f != fidx]
            competitor_track = [d for d, i, f in eligible if f == fidx and i != idx]
            if ((not competitor_row or min(competitor_row) - dist >= AMBIGUITY_MARGIN)
                and (not competitor_track or
                     min(competitor_track) - dist >= AMBIGUITY_MARGIN)):
                picked.append((dist, idx, fidx))
        used_rows, used_tracks = set(), set()
        for dist, idx, fidx in picked:
            if idx in used_rows or fidx in used_tracks:
                continue
            track = fragments[fidx]
            row = current[idx]
            track["end_frame"] = frame
            track["ground_xy"] = row["ground_xy"]
            track["observations"].append(row)
            used_rows.add(idx)
            used_tracks.add(fidx)
        for idx, row in enumerate(current):
            if idx in used_rows:
                continue
            key = "V%03d" % (len(fragments) + 1)
            fragments.append({
                "fragment_id": key, "start_frame": frame,
                "end_frame": frame, "ground_xy": row["ground_xy"],
                "observations": [row],
            })
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
