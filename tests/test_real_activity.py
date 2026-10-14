"""Bounded real dual-camera activity; scientific GT is never runtime input.

These small constructed inputs are negative/contract UNIT tests only.
The owner CI decodes official EPFL AVIs for media proof.
"""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app import real_activity


def _row(key, frame, x0, y0, x1, y1):
    return {"candidate_key":key, "frame_0_based":frame,
            "cam0_box_index":0,"cam1_box_index":0,
            "ground_distance":round(((x1-x0)**2+(y1-y0)**2)**0.5,3),
            "cam0_ground_x":x0,"cam0_ground_y":y0,
            "cam1_ground_x":x1,"cam1_ground_y":y1,
            "verdict":None}


def test_unambiguous_temporal_fragments_and_no_overcount_of_people():
    rows = [_row("a",500,10,10,12,10), _row("b",525,11,10,13,10),
            _row("c",500,200,100,202,100),
            _row("d",525,201,100,203,100)]
    result = real_activity.activity_summary(rows,25.,temporal_verified=True)
    assert result["matched_pair_observations"] == 4
    assert result["sampled_video_instants"] == 2
    assert result["anonymous_activity_fragments"] == 2
    assert result["multi_instant_fragments"] == 2
    assert sorted(x["synchronized_pair_observations"] for x in result["activities"]) == [2,2]
    assert result["timeline"][0]["video_second"] == 20.


def test_strict_real_activity_demo_alerts_only_on_approved_links():
    rows=[_row("a",500,10,10,12,10),_row("b",525,11,10,13,10)]
    summary=real_activity.activity_summary(rows,25.,temporal_verified=True)
    assert summary["multi_instant_fragments"]==1
    data={"ready":True,"cross_time_temporal_linkage_verified":True,**summary}
    alerts=real_activity.build_demo_activity_alerts(data)
    assert len(alerts)==1
    assert alerts[0]["rule_is_demo"] is True
    assert alerts[0]["alert_is_real_incident"] is False
    assert alerts[0]["source_evidence_is_real_video"] is True
    assert alerts[0]["supporting_candidate_keys"] == ["a","b"]
    assert real_activity.build_demo_activity_alerts({
        **data,"cross_time_temporal_linkage_verified":False}) == []


def test_ambiguous_or_missing_evidence_never_merged_as_identity():
    a = _row("a",500,0,0,2,0)
    b = _row("b",500,20,0,22,0)
    c = _row("c",625,10,0,12,0)  # equally plausible between a/b
    d = _row("d",750,12,0,14,0)
    d["cam1_ground_x"] = None
    tracks = real_activity.track_fragments([a,b,c,d])
    assert len(tracks) == 4
    assert all(len(t["observations"]) == 1 for t in tracks)


@pytest.fixture
def auth(tmp_path, monkeypatch):
    from app import main, access, epfl_review
    monkeypatch.delenv("ZD_AUTH_TEST_COMPAT",raising=False)
    monkeypatch.setattr(main, "DB", tmp_path/"research.sqlite")
    main.setup_db()
    with main.db() as db:
        root = db.execute("SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()[0]
        county = db.execute("SELECT id FROM regions WHERE level=2 LIMIT 1").fetchone()[0]
        access.create_user(db,"real_area_admin","long-valid-research-admin-pass",root)
        access.create_user(db,"real_area_county","long-valid-research-county-pass",county)
    client=TestClient(main.app)
    def login(user,password):
        r=client.post("/api/auth/login",json={"username":user,"password":password})
        assert r.status_code == 200
        return {"Authorization":"Bearer "+r.json()["access_token"]}
    root_hdr=login("real_area_admin","long-valid-research-admin-pass")
    county_hdr=login("real_area_county","long-valid-research-county-pass")
    return main,epfl_review,client,root_hdr,county_hdr,tmp_path


def _manifest():
    from app.epfl_review import CAMERA_SHA256
    def sample(frame, a, b):
        return {"frame_0_based":frame,"cross_camera_candidates":1,
                "candidate_evidence":[{
                    "cam0_model_box_index":0,"cam1_model_box_index":0,
                    "calibration_distance":2.0,
                    "cam0_ground_xy":[a,10.],
                    "cam1_ground_xy":[b,10.],
                    "audit_gt_cam0":2,"audit_gt_cam1":2}]}
    return {"schema_version":"epfl6p_anonymous_candidate_bundle_v1",
            "dataset":"laboratory_six_person_four_synced_views",
            "selected_cameras":[0,1], "model":"yolo11n.pt",
            "matching_algorithm":"fixed-camera calibrated-ground-plane Hungarian gate",
            "matching_gt_is_never_input":True,
            "production_real_roster_or_alerts":False,
            "generated_from":"real_video_owner_ci_detector_result",
            "per_camera":{"0":{"sha256":CAMERA_SHA256[0],"fps":25.},
                          "1":{"sha256":CAMERA_SHA256[1],"fps":25.}},
            "frame_audits":[sample(500,10.,12.)],
            "holdout_frame_audits":[sample(625,11.,13.)]}


def test_admin_must_import_real_video_provenance_and_never_use_GT(auth, monkeypatch):
    main,epfl,client,admin,county,tmp_path=auth
    assert client.get("/api/real/activity").status_code==401
    assert client.get("/api/real/activity",headers=county).status_code==403
    empty=client.get("/api/real/activity",headers=admin).json()
    assert not empty["ready"] and empty["anonymous_activity_fragments"]==0
    empty_alerts=client.get("/api/real/demo-alerts",headers=admin).json()
    assert empty_alerts["alerts"]==[]
    assert client.get("/api/real/demo-alerts").status_code==401
    assert client.get("/api/real/demo-alerts",headers=county).status_code==403
    raw=_manifest()
    # This test includes audit labels solely to prove sanitization. The actual
    # verified owner output is generated from original video elsewhere.
    report=tmp_path/"candidate.json"
    report.write_text(json.dumps(raw),encoding="utf-8")
    monkeypatch.setattr(epfl,"BUNDLED_CANDIDATES",report)
    result=client.post("/api/research/epfl/bundled/import",headers=admin,json={})
    assert result.status_code==201,result.text
    d=client.get("/api/real/activity",headers=admin).json()
    assert d["ready"] is True
    assert d["matched_pair_observations"]==2
    assert d["sampled_video_instants"]==2
    assert d["anonymous_activity_fragments"]==2
    assert d["multi_instant_fragments"]==0
    assert d["cross_time_temporal_linkage_verified"] is False
    demo=client.get("/api/real/demo-alerts",headers=admin).json()
    assert demo["alerts"]==[]
    assert demo["rule_is_demo"] is True
    assert d["activities"][0]["camera_count"]==2
    assert d["activities"][0]["first_video_second"]==20.
    assert d["confirmed_natural_person_identity"] is False
    assert d["verified_unique_people"] is None
    assert d["real_cross_site_visits"] is None
    assert d["real_identity_alerts"]==[]
    assert "audit_gt" not in json.dumps(d)
    with main.db() as db:
        assert db.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM synthetic_registry_runs").fetchone()[0]==0
    # Research candidate explicitly rejected by reviewer no longer counted.
    candidates=client.get("/api/research/epfl/candidates",headers=admin).json()
    rejected=candidates["candidates"][0]["candidate_key"]
    r=client.post("/api/research/epfl/reviews",headers=admin,json={
        "candidate_key":rejected,
        "verdict":"false_match",
        "reason":"两侧位置虽近但是研究审查明确不是同一目标"})
    assert r.status_code==201,r.text
    d=client.get("/api/real/activity",headers=admin).json()
    assert d["matched_pair_observations"]==1
    assert d["excluded_manual_false_matches"]==1


def test_reject_fake_or_inconsistent_detector_coordinates(auth,monkeypatch):
    main,epfl,client,admin,county,tmp_path=auth
    raw=_manifest()
    raw["frame_audits"][0]["candidate_evidence"][0]["cam1_ground_xy"]=[50.,10.]
    r=client.post("/api/research/epfl/import",headers=admin,json=raw)
    assert r.status_code==422
    raw=_manifest()
    raw["frame_audits"][0]["candidate_evidence"][0]["cam1_ground_xy"]=["not-a-coordinate",10.]
    r=client.post("/api/research/epfl/import",headers=admin,json=raw)
    assert r.status_code==422

def test_seven_original_excel_alerts_are_actual_rule_engine_results():
    from app.demo_excel_alerts import demo_excel_rule_analysis
    from app.business import RULE_NAMES
    out=demo_excel_rule_analysis()
    assert out["mode"] == "synthetic_only"
    assert out["based_on_real_video"] is False
    assert out["no_mutation_to_real_database"] is True
    assert {row["type"] for row in out["alerts"]} == set(RULE_NAMES)
    assert all(row["origin"]=="synthetic_only" for row in out["alerts"])
    assert sum(row["count"] for row in out["rule_counts"])==len(out["alerts"])


def test_original_excel_23_items_read_only_real_mode_and_admin_scope(auth):
    main,epfl,client,admin,county,tmp_path=auth
    assert client.get("/api/real/excel-acceptance").status_code==401
    assert client.get("/api/real/excel-acceptance",headers=county).status_code==403
    r=client.get("/api/real/excel-acceptance",headers=admin)
    assert r.status_code==200,r.text
    data=r.json()
    assert [x["id"] for x in data["requirements"]]==list(range(1,24))
    assert data["status_counts"]=={
        "verified_real":3,"synthetic_only":10,"partial":8,"missing":2}
    assert data["all_23_fully_accepted"] is False
    assert all(x["demo_route"].startswith(("/real","/cctv","/admin"))
               for x in data["requirements"])
    assert {x["id"] for x in data["requirements"]
            if x["status"]=="missing"}=={14,17}
    with main.db() as db:
        assert db.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]==0


def test_simulated_excel_alerts_are_admin_only_and_not_written_to_db(auth):
    main,epfl,client,admin,county,tmp_path=auth
    assert client.get("/api/real/excel-rule-demo").status_code == 401
    assert client.get("/api/real/excel-rule-demo",headers=county).status_code == 403
    response=client.get("/api/real/excel-rule-demo",headers=admin)
    assert response.status_code==200,response.text
    data=response.json()
    assert len(data["rule_counts"])==7
    assert {x["type"] for x in data["alerts"]}==set(
        __import__('app.business',fromlist=['RULE_NAMES']).RULE_NAMES)
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM synthetic_alert_reviews').fetchone()[0]==0



def test_real_video_scene_aggregate_is_anonymous_and_has_no_fake_gps():
    from app.real_scene_aggregate import summarize_scene
    # These values are a deterministic UNIT fixture, not actual EPFL media.
    rows=[_row("a",500,120,150,122,150),
          _row("b",500,180,110,182,110),
          _row("c",500,240,210,242,210),
          _row("d",525,140,180,142,180)]
    facts=real_activity.activity_summary(rows,25.,temporal_verified=False)
    result=summarize_scene({"ready":True,**facts},minimum_observations=3)
    assert result["total_observations"]==4
    assert len(result["demo_scene_events"])==1
    assert result["demo_scene_events"][0]["video_frame"]==500
    assert result["demo_scene_events"][0]["paired_detection_count"]==3
    assert sum(x["paired_detection_count"] for x in result["heat_cells"])==4
    assert result["wgs84_available"] is False
    assert result["real_cross_location_identity_available"] is False
    assert result["geographic_trajectory_available"] is False
    assert result["warning_is_confirmed_risk"] is False
    assert "candidate_key" not in json.dumps(result)
    assert "anonymous_fragment_id" not in json.dumps(result)
    assert "latitude" not in json.dumps(result)
    assert result["grid_size"]==8
    assert result["zone"]=={"x_bin":2,"y_bin":3,"width_cells":2,"height_cells":2}
    assert len(result["frame_cells"])==2
    assert len(result["demo_zone_events"])==2
    assert [e["video_frame"] for e in result["demo_zone_events"]]==[500,525]
    assert result["individual_trajectory_available"] is False
    assert result["frame_cells"][0]["paired_detection_count"]==3
    assert sum(c["paired_detection_count"] for c in result["frame_cells"][0]["cells"])==3
    assert all("fragment_id" not in json.dumps(frame)
               for frame in result["frame_cells"])
    # Moving the demo rectangle recomputes against the SAME observed cells.
    moved=summarize_scene({"ready":True,**facts},minimum_observations=3,
                          zone_x_bin=6,zone_y_bin=5)
    assert len(moved["demo_zone_events"])==1
    assert moved["demo_zone_events"][0]["video_frame"]==500
    assert moved["total_observations"]==result["total_observations"]


def test_real_video_scene_aggregate_requires_real_import_and_admin(auth,monkeypatch):
    from app.real_scene_aggregate import summarize_scene
    main,epfl,client,admin,county,tmp_path=auth
    assert client.get("/api/real/scene-aggregate").status_code==401
    assert client.get("/api/real/scene-aggregate",headers=county).status_code==403
    empty=client.get("/api/real/scene-aggregate",headers=admin).json()
    assert not empty["ready"] and empty["total_observations"]==0
    assert not empty["demo_scene_events"]
    raw=_manifest()
    package=tmp_path/"from-real-footage-ci-bundle.json"
    package.write_text(json.dumps(raw),encoding="utf-8")
    monkeypatch.setattr(epfl,"BUNDLED_CANDIDATES",package)
    r=client.post("/api/research/epfl/bundled/import",headers=admin,json={})
    assert r.status_code==201
    result=client.get("/api/real/scene-aggregate?minimum_observations=1",
                      headers=admin).json()
    assert result["ready"] and result["total_observations"]==2
    assert len(result["demo_scene_events"])==2
    assert len(result["heat_cells"])>=1
    assert client.get("/api/real/scene-aggregate?minimum_observations=11",
                      headers=admin).status_code==422
    zone=client.get("/api/real/scene-aggregate?zone_x_bin=0&zone_y_bin=0"
                    "&minimum_observations=1",headers=admin).json()
    assert len(zone["demo_zone_events"])==2
    assert len(zone["frame_cells"])==2
    assert zone["zone"]["x_bin"]==0
    assert client.get("/api/real/scene-aggregate?zone_x_bin=7",
                      headers=admin).status_code==422
    with main.db() as db:
        assert db.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM synthetic_alert_reviews").fetchone()[0]==0


def test_review_rejection_invalidates_the_old_ground_truth_temporal_certification(auth,monkeypatch):
    """Removing any candidate changes mutual-nearest alternatives.

    A once-correct 14-link benchmark cannot certify a different post-review
    graph. Unit labels here are artificial contract fixtures, not real video.
    """
    main,epfl,client,admin,county,tmp_path=auth
    raw=_manifest()
    raw["holdout_frame_audits"][0]["frame_0_based"]=525
    raw["temporal_fragment_verified"]=True
    bundle=tmp_path/"review-test-only-bundle.json"
    bundle.write_text(json.dumps(raw),encoding="utf-8")
    monkeypatch.setattr(epfl,"BUNDLED_CANDIDATES",bundle)
    assert client.post("/api/research/epfl/bundled/import",
                       headers=admin,json={}).status_code==201
    pre=client.get("/api/real/activity",headers=admin).json()
    assert pre["cross_time_temporal_linkage_verified"] is True
    assert pre["multi_instant_fragments"]==1
    assert len(client.get("/api/real/demo-alerts",headers=admin).json()["alerts"])==1
    key=client.get("/api/research/epfl/candidates",
                   headers=admin).json()["candidates"][0]["candidate_key"]
    rv=client.post("/api/research/epfl/reviews",headers=admin,json={
        "candidate_key":key,"verdict":"false_match",
        "reason":"此配对已确认错误，必须关闭旧关联真值标记"})
    assert rv.status_code==201
    post=client.get("/api/real/activity",headers=admin).json()
    assert post["matched_pair_observations"]==1
    assert post["cross_time_temporal_linkage_verified"] is False
    assert post["cross_time_verification_invalidated_by_rejection"] is True
    assert post["multi_instant_fragments"]==0
    assert "recertification_required" in post["cross_time_status"]
    assert client.get("/api/real/demo-alerts",headers=admin).json()["alerts"]==[]
    assert client.get("/api/real/scene-aggregate",
                      headers=admin).json()["short_time_linkage_separately_qualified"] is False
    assert client.get("/api/real/scene-aggregate",
                      headers=admin).json()["short_anonymous_tracklets"] == []


def test_negative_candidate_removal_can_create_new_unscored_links():
    a=_row("a",500,0,0,2,0)
    b=_row("b",500,20,0,22,0)
    c=_row("c",525,13,0,15,0)
    def links(rs):
        return {tuple(o["candidate_key"] for o in frag["observations"])
                for frag in real_activity.track_fragments(rs)
                if len(frag["observations"])>1}
    assert not links([a,b,c])
    assert links([a,c])=={("a","c")}


def test_very_short_cross_camera_tracklets_are_video_grounded_and_never_gps():
    from app.real_scene_aggregate import summarize_scene
    observations=[
        _row("first",500,120,150,122,150),
        _row("second",525,122,152,124,152),
    ]
    verified=real_activity.activity_summary(observations,25.,
                                            temporal_verified=True)
    assert verified["multi_instant_fragments"]==1
    scene=summarize_scene({
        "ready":True, "cross_time_temporal_linkage_verified":True,
        **verified})
    assert scene["short_anonymous_tracklets_verified"] is True
    assert len(scene["short_anonymous_tracklets"])==1
    track=scene["short_anonymous_tracklets"][0]
    assert track["site_kind"]=="same_real_laboratory_two_views"
    assert track["camera_pair"]==["6p-c0.avi","6p-c1.avi"]
    assert [p["video_frame"] for p in track["points"]]==[500,525]
    assert [p["candidate_key"] for p in track["points"]]==["first","second"]
    assert track["identity_verified"] is False
    assert track["real_geographic_journey"] is False
    assert scene["wgs84_available"] is False
    assert scene["individual_trajectory_available"] is False
    assert scene["real_cross_location_identity_available"] is False
    downgraded=summarize_scene({
        "ready":True, "cross_time_temporal_linkage_verified":False,
        **{**verified,"cross_time_temporal_linkage_verified":False}})
    assert downgraded["short_anonymous_tracklets_verified"] is False
    assert downgraded["short_anonymous_tracklets"]==[]
    assert downgraded["total_observations"]==2

