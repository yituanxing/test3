"""Real EPFL scene counts plus bounded, audited anonymous short tracklets.

Long-gap identity, real-person biographical identity, geographic travel and
GPS are unavailable. Research-admin-only output has source-frame evidence.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from math import isfinite
from pathlib import Path
from fastapi import APIRouter, Query, Request, HTTPException
from fastapi.responses import FileResponse
from app import epfl_review
from app.real_activity import real_activity

router=APIRouter()
GRID_SIZE=8
CELL_WIDTH=40


def summarize_scene(activity, *, minimum_observations=3,
                    zone_x_bin=2, zone_y_bin=3):
    """Return spatially aggregated observations, playback frames and demo rules.

    A 2x2 coarse-grid zone is user-positionable. Detection observations are
    never turned into unique people, person locations or path segments.
    """
    if not 1<=minimum_observations<=10:
        raise ValueError("Invalid simultaneous detection threshold")
    if not 0<=zone_x_bin<=6 or not 0<=zone_y_bin<=6:
        raise ValueError("Zone must fit the 8x8 visualization grid")
    result={
        "ready":bool(activity.get("ready")),
        "coordinate_system":"EPFL calibrated local video plane, coarse 8x8 grid; NOT GPS",
        "spatial_reference_kind":"video_projected_scene_only",
        "grid_size":GRID_SIZE,"grid_cell_width_in_projection_units":CELL_WIDTH,
        "zone":{"x_bin":zone_x_bin,"y_bin":zone_y_bin,
                "width_cells":2,"height_cells":2},
        "wgs84_available":False,"geographic_trajectory_available":False,
        "real_cross_location_identity_available":False,
        "individual_trajectory_available":False,
        "short_anonymous_tracklets_verified":False,
        "short_anonymous_tracklets":[],
        "short_tracklets_excluded_from_map":0,
        "virtual_zone_division":{"axis":"projected_y","threshold":150.0,
             "above":"室内虚拟A区","below":"室内虚拟B区",
             "true_different_sites":False},
        "short_virtual_zone_crossings":[],
        "short_virtual_zone_crossing_count":0,
        "short_time_linkage_separately_qualified":bool(
            activity.get("cross_time_temporal_linkage_verified")),
        "source_video_is_real":bool(activity.get("ready")),
        "warning_is_confirmed_risk":False,
        "identity_claim":False,
        "rule_type":"demonstration threshold over actual paired video detections",
        "heat_cells":[],"time_bins":[],"frame_cells":[],
        "demo_scene_events":[],"demo_zone_events":[],
        "total_observations":0,"observations_outside_coarse_grid":0,
        "note":"8x8局部视频投影示意，不是真实GPS或场地测绘。逐帧格子展示观测活动变化；"
               "不能据此断言同一人的移动路线。区域规则触发不等于风险事件。",
    }
    if not result["ready"]:
        return result

    heat=Counter()
    frame_counts=defaultdict(int)
    frame_cells=defaultdict(Counter)
    out_of_grid=0
    for fragment in activity.get("activities",[]):
        for sample in fragment.get("samples",[]):
            frame=int(sample["frame_0_based"])
            frame_counts[frame]+=1
            xy=sample.get("common_ground_xy")
            if (isinstance(xy,(tuple,list)) and len(xy)==2
                and all(isinstance(v,(int,float)) and isfinite(v) for v in xy)
                and 0<=xy[0]<GRID_SIZE*CELL_WIDTH
                and 0<=xy[1]<GRID_SIZE*CELL_WIDTH):
                cell=(int(xy[0]//CELL_WIDTH),int(xy[1]//CELL_WIDTH))
                heat[cell]+=1
                frame_cells[frame][cell]+=1
            else:
                out_of_grid+=1

    result["heat_cells"]=[
        {"x_bin":x,"y_bin":y,"paired_detection_count":count}
        for (x,y),count in sorted(heat.items())]
    result["time_bins"]=[
        {"video_frame":frame,"paired_detection_count":n}
        for frame,n in sorted(frame_counts.items())]
    result["frame_cells"]=[
        {"video_frame":frame,"paired_detection_count":frame_counts[frame],
         "cells":[{"x_bin":x,"y_bin":y,"paired_detection_count":count}
                  for (x,y),count in sorted(frame_cells[frame].items())]}
        for frame in sorted(frame_counts)]
    result["demo_scene_events"]=[
        {"video_frame":frame,"paired_detection_count":n,
         "threshold":minimum_observations,
         "simulation_rule":True,"actual_person_risk":False}
        for frame,n in sorted(frame_counts.items())
        if n>=minimum_observations]
    zone_xs=range(zone_x_bin,zone_x_bin+2)
    zone_ys=range(zone_y_bin,zone_y_bin+2)
    result["demo_zone_events"]=[
        {"video_frame":frame,
         "paired_detection_count_in_zone":sum(
             count for (x,y),count in frame_cells[frame].items()
             if x in zone_xs and y in zone_ys),
         "simulation_rule":True,"actual_person_risk":False,
         "zone_is_illustrative":True}
        for frame in sorted(frame_counts)
        if any(count and x in zone_xs and y in zone_ys
               for (x,y),count in frame_cells[frame].items())]
    # Linkable, BOUNDED anonymous two-camera motion is a distinct class of
    # evidence from aggregated heat counts. Only the original untouched
    # GT-scored 25-frame graph may be used. No invented geolocation, no
    # inference through an unobserved gap, no named person or cross-site trip.
    if activity.get("cross_time_temporal_linkage_verified") is True:
        eligible=[]
        for fragment in activity.get("activities",[]):
            if not fragment.get("cross_frame_linked"):
                continue
            samples=sorted(fragment.get("samples",[]),
                           key=lambda p:p["frame_0_based"])
            if len(samples)<2:
                continue
            if any(b["frame_0_based"]-a["frame_0_based"]!=25
                   for a,b in zip(samples,samples[1:])):
                continue
            points=[]
            for sample in samples:
                xy=sample.get("common_ground_xy")
                if (not isinstance(xy,(list,tuple)) or len(xy)!=2
                    or not all(isinstance(v,(int,float)) and isfinite(v)
                               for v in xy)):
                    points=[]
                    break
                points.append({
                    "video_frame":int(sample["frame_0_based"]),
                    "video_second":sample.get("video_second"),
                    "x":float(xy[0]),"y":float(xy[1]),
                    "candidate_key":sample["candidate_key"],
                    "cam0_box_index":sample["cam0_box_index"],
                    "cam1_box_index":sample["cam1_box_index"],
                })
            if points:
                eligible.append({
                    "fragment_id":fragment["activity_id"],
                    "camera_pair":["6p-c0.avi","6p-c1.avi"],
                    "site_kind":"same_real_laboratory_two_views",
                    "short_association_only":True,
                    "identity_verified":False,
                    "real_geographic_journey":False,
                    "points":points,
                })
        result["short_anonymous_tracklets"]=eligible
        result["short_anonymous_tracklets_verified"]=True
        result["short_tracklets_excluded_from_map"]=sum(
            any(p["x"]<0 or p["x"]>=320 or p["y"]<0 or p["y"]>=320
                for p in line["points"])
            for line in eligible)
        # A/B are explicitly VIRTUAL areas within ONE EPFL laboratory.
        # Coordinates and matches are actual YOLO/geometry-derived video
        # observations; the split line is merely a demonstrative rule.
        crossings=[]
        for segment in eligible:
            for a,b in zip(segment["points"],segment["points"][1:]):
                from_a=a["y"]<150.0
                to_a=b["y"]<150.0
                if from_a==to_a:
                    continue
                crossings.append({
                    "fragment_id":segment["fragment_id"],
                    "from_virtual_area":"室内虚拟A区" if from_a else "室内虚拟B区",
                    "to_virtual_area":"室内虚拟A区" if to_a else "室内虚拟B区",
                    "source_frames":[a["video_frame"],b["video_frame"]],
                    "source_candidate_keys":[a["candidate_key"],b["candidate_key"]],
                    "real_video_evidence":True,
                    "virtual_area_rule_only":True,
                    "different_real_world_sites_verified":False,
                    "gps_track":False,
                })
        result["short_virtual_zone_crossings"]=crossings
        result["short_virtual_zone_crossing_count"]=len(crossings)
    result["total_observations"]=sum(frame_counts.values())
    result["observations_outside_coarse_grid"]=out_of_grid
    return result


# The clip is never checked into the public source repository. It may be
# included only in a separately attributed RESEARCH-USE local presentation ZIP.
# The final user-facing page fails visibly when the clip isn't installed.
SHOWCASE_VIDEO = Path(__file__).resolve().parents[1] / "samples" / "demo-media" / "epfl-dual-60-98.mp4"


@router.get("/api/real/showcase-media/status")
def research_showcase_media_status(request: Request):
    epfl_review._admin(request)
    is_ready = SHOWCASE_VIDEO.is_file() and SHOWCASE_VIDEO.stat().st_size > 100_000
    return {
        "ready": is_ready,
        "kind": "actual_continuous_epfl_two_camera_original_video_excerpt" if is_ready else None,
        "duration_seconds": 38 if is_ready else None,
        "clip_starts_at_original_second": 60 if is_ready else None,
        "research_use_only": True,
        "two_cameras_same_physical_lab": True,
        "source_original_video_copyright": "CVLab-EPFL",
        "not_live_camera": True,
        "note": "视频文件不随公开源码发布。离线研究演示包可包含授权条件下的38秒节选。" if not is_ready
                else "两台摄像机的真实连续同期原片节选；本机播放的是历史研究数据，而不是监控直播。"
    }


@router.get("/api/real/showcase-media/video")
def research_showcase_media_video(request: Request):
    epfl_review._admin(request)
    if not SHOWCASE_VIDEO.is_file() or SHOWCASE_VIDEO.stat().st_size < 100_000:
        raise HTTPException(404,"研究视频尚未安装；可从最终研究展示包解压到samples/demo-media/")
    return FileResponse(SHOWCASE_VIDEO,media_type="video/mp4",
                        headers={"Cache-Control":"private, max-age=0",
                                 "X-Content-Type-Options":"nosniff"})


@router.get("/api/real/scene-aggregate")
def real_scene_aggregate(request: Request,
                         minimum_observations:int=Query(3,ge=1,le=10),
                         zone_x_bin:int=Query(2,ge=0,le=6),
                         zone_y_bin:int=Query(3,ge=0,le=6)):
    return summarize_scene(real_activity(request),
                           minimum_observations=minimum_observations,
                           zone_x_bin=zone_x_bin,zone_y_bin=zone_y_bin)


def register(app):
    app.include_router(router)
