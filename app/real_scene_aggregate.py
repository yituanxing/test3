"""Anonymized 8x8 indoor scene display computed from genuine EPFL video.

This is an intentionally COARSE demonstration grid, not surveyed floorplan,
GPS, or anyone's position/route. Each sampled time contains aggregate detector
PAIR counts; it cannot identify or track a person between times. Zone events
are illustrative business RULES applied to actual video-derived observations.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from math import isfinite
from fastapi import APIRouter, Query, Request
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
    result["total_observations"]=sum(frame_counts.values())
    result["observations_outside_coarse_grid"]=out_of_grid
    return result


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
