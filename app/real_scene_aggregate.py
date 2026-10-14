"""Video-derived, non-identifying aggregate indoor scene statistics.

This module does not perform human identification, individual tracking,
geolocation, or risk scoring. It never exposes a person's position. Counts
reflect camera-paired detections, not unique visitors. No synthetic observations
are inserted into the real media database.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from fastapi import APIRouter, Query, Request
from app.real_activity import real_activity

router=APIRouter()

def summarize_scene(activity, *, minimum_observations=3):
    """Aggregate paired detection positions into anonymous heat cells."""
    result={
        "ready":bool(activity.get("ready")),
        "coordinate_system":"calibrated video plane; not geographic GPS",
        "wgs84_available":False,
        "geographic_trajectory_available":False,
        "real_cross_location_identity_available":False,
        "rule_type":"demonstration simultaneous detection count",
        "source_video_is_real":bool(activity.get("ready")),
        "warning_is_confirmed_risk":False,
        "identity_claim":False,
        "heat_cells":[], "time_bins":[], "demo_scene_events":[],
        "total_observations":0,
    }
    if not result["ready"]:
        return result
    heat=Counter()
    frame_counts=defaultdict(int)
    for fragment in activity.get("activities",[]):
        for sample in fragment.get("samples",[]):
            xy=sample.get("common_ground_xy")
            if isinstance(xy,list) and len(xy)==2:
                x,y=xy
                if isinstance(x,(int,float)) and isinstance(y,(int,float)):
                    # Fixed visual grid is not a physical survey or GPS map.
                    if 0<=x<320 and 0<=y<320:
                        heat[(int(x//40),int(y//40))]+=1
            frame_counts[int(sample["frame_0_based"])]+=1
    result["heat_cells"]=[
        {"x_bin":x,"y_bin":y,"paired_detection_count":count}
        for (x,y),count in sorted(heat.items())]
    result["time_bins"]=[
        {"video_frame":frame,"paired_detection_count":n}
        for frame,n in sorted(frame_counts.items())]
    result["demo_scene_events"]=[
        {"video_frame":frame,"paired_detection_count":n,
         "threshold":minimum_observations,
         "simulation_rule":True,"actual_person_risk":False}
        for frame,n in sorted(frame_counts.items())
        if n>=minimum_observations]
    result["total_observations"]=sum(frame_counts.values())
    return result


@router.get("/api/real/scene-aggregate")
def real_scene_aggregate(request: Request,
                         minimum_observations:int=Query(3,ge=1,le=10)):
    return summarize_scene(real_activity(request),
                           minimum_observations=minimum_observations)


def register(app):
    app.include_router(router)
