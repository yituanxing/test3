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
    rows = [_row("a",500,10,10,12,10), _row("b",625,11,10,13,10),
            _row("c",500,200,100,202,100),
            _row("d",625,201,100,203,100)]
    result = real_activity.activity_summary(rows,25.)
    assert result["matched_pair_observations"] == 4
    assert result["sampled_video_instants"] == 2
    assert result["anonymous_activity_fragments"] == 2
    assert result["multi_instant_fragments"] == 2
    assert sorted(x["synchronized_pair_observations"] for x in result["activities"]) == [2,2]
    assert result["timeline"][0]["video_second"] == 20.


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
    assert d["anonymous_activity_fragments"]==1
    assert d["multi_instant_fragments"]==1
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
