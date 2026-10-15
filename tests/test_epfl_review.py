"""EPFL cross-camera research workflow: persistence, scope and no identity promotion."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_and_users(tmp_path, monkeypatch):
    from app import main, access
    monkeypatch.delenv("ZD_AUTH_TEST_COMPAT", raising=False)
    monkeypatch.setattr(main, "DB", tmp_path/"epfl-review.sqlite")
    main.setup_db()
    with main.db() as c:
        root = c.execute("SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()[0]
        lower = c.execute("SELECT id FROM regions WHERE level=2 LIMIT 1").fetchone()[0]
        access.create_user(c,"epfl_admin","temporary-epfl-root-password",root)
        access.create_user(c,"epfl_county","temporary-epfl-county-password",lower)
    cli = TestClient(main.app)
    def token(name,password):
        r=cli.post("/api/auth/login",json={"username":name,"password":password})
        assert r.status_code == 200,r.text
        return {"Authorization":"Bearer "+r.json()["access_token"]}
    return (main,cli,token("epfl_admin","temporary-epfl-root-password"),
            token("epfl_county","temporary-epfl-county-password"))


def sample():
    from app.epfl_review import CAMERA_SHA256
    def frame(n,a=0,b=1):
        return {"frame_0_based":n,"cross_camera_candidates":1,
                "candidate_evidence":[{
                 "cam0_model_box_index":a,"cam1_model_box_index":b,
                 "calibration_distance":9.74,
                 "audit_gt_cam0":3,"audit_gt_cam1":3}]}
    return {"dataset":"laboratory_six_person_four_synced_views",
           "model":"yolo11n.pt",
           "matching_algorithm":"fixed-camera calibrated-ground-plane Hungarian gate",
           "matching_gt_is_never_input":True,
           "production_real_roster_or_alerts":False,
           "selected_cameras":[0,1],
           "per_camera":{"0":{"sha256":CAMERA_SHA256[0]},
                         "1":{"sha256":CAMERA_SHA256[1]}},
           "frame_audits":[frame(500)],"holdout_frame_audits":[frame(525)]}


def test_import_is_admin_only_and_never_exposes_official_id(client_and_users):
    main,cli,root,county=client_and_users
    path="/api/research/epfl/import"
    assert cli.get("/api/research/epfl/candidates").status_code==401
    assert cli.get("/api/research/epfl/candidates",headers=county).status_code==403
    assert cli.post(path,json=sample()).status_code==401
    assert cli.post(path,headers=county,json=sample()).status_code==403
    imported=cli.post(path,headers=root,json=sample())
    assert imported.status_code==201,imported.text
    assert imported.json()["candidate_count"]==2
    assert imported.json()["feeds_real_alerts"] is False
    assert cli.post(path,headers=root,json=sample()).json()["already_present"] is True
    list_result=cli.get("/api/research/epfl/candidates?limit=1",headers=root).json()
    assert list_result["reviewed_count"]==0
    assert list_result["next_offset"]==1
    all_rows=cli.get("/api/research/epfl/candidates",headers=root).json()
    assert len(all_rows["candidates"])==2
    assert not any("audit_gt_" in str(row) for row in all_rows["candidates"])
    with main.db() as c:
        assert c.execute("SELECT COUNT(*) FROM epfl_pair_candidates").fetchone()[0]==2
        assert c.execute("SELECT COUNT(*) FROM epfl_candidate_reports").fetchone()[0]==1
        assert c.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM synthetic_registry_runs").fetchone()[0]==0


def test_reviews_append_only_and_prohibit_identity_outcome(client_and_users):
    main,cli,root,county=client_and_users
    assert cli.post("/api/research/epfl/import",headers=root,json=sample()).status_code==201
    pairs=cli.get("/api/research/epfl/candidates",headers=root).json()["candidates"]
    key=pairs[0]["candidate_key"]
    endpoint="/api/research/epfl/reviews"
    payload={"candidate_key":key,"verdict":"plausible_same_target",
             "reason":"查看同步几何位置后认为可能是同一个匿名人体目标"}
    assert cli.post(endpoint,headers=county,json=payload).status_code==403
    assert cli.post(endpoint,headers=root,json={
        **payload,"verdict":"real_person_identity_verified"}).status_code==422
    assert cli.post(endpoint,headers=root,json={
        **payload,"candidate_key":"f"*32}).status_code==404
    assert cli.post(endpoint,headers=root,json={
        **payload,"reason":"bad"}).status_code==422
    first=cli.post(endpoint,headers=root,json=payload)
    assert first.status_code==201,first.text
    assert first.json()["identity_verified"] is False
    assert first.json()["feeds_roster"] is False
    second=cli.post(endpoint,headers=root,json={
        **payload,"verdict":"false_match","reason":"二次复核发现此候选不能被确认为同一目标"})
    assert second.status_code==201,second.text
    current=cli.get("/api/research/epfl/candidates",headers=root).json()
    assert current["reviewed_count"]==1
    assert current["candidates"][0]["verdict"]=="false_match"
    with main.db() as c:
        assert c.execute("SELECT COUNT(*) FROM epfl_pair_reviews").fetchone()[0]==2
        assert c.execute("SELECT COUNT(*) FROM admin_audit WHERE operation='research_epfl_candidate_review'").fetchone()[0]==2
        assert c.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM synthetic_alert_reviews").fetchone()[0]==0
    main.setup_db()
    assert cli.get("/api/research/epfl/candidates",headers=root).json()["reviewed_count"]==1



def test_bundled_real_media_format_is_one_click_idempotent_and_admin_only(
        client_and_users, tmp_path, monkeypatch):
    import json
    from app import epfl_review
    main,cli,root,county=client_and_users
    bundle=sample()
    bundle["schema_version"]="epfl6p_anonymous_candidate_bundle_v1"
    bundle["generated_from"]="real_video_owner_ci_detector_result"
    # No official labels may enter the deployed matching runtime.
    for k in ("frame_audits","holdout_frame_audits"):
        for row in bundle[k]:
            for pair in row["candidate_evidence"]:
                pair.pop("audit_gt_cam0",None)
                pair.pop("audit_gt_cam1",None)
    manifest=tmp_path/"ci-real-pairs.json"
    manifest.write_text(json.dumps(bundle),encoding="utf-8")
    monkeypatch.setattr(epfl_review,"BUNDLED_CANDIDATES",manifest)
    path="/api/research/epfl/bundled"
    assert cli.get(path).status_code==401
    assert cli.get(path,headers=county).status_code==403
    assert cli.get(path,headers=root).json()["candidate_count"]==2
    assert cli.post(path+"/import",headers=county,json={}).status_code==403
    inserted=cli.post(path+"/import",headers=root,json={})
    assert inserted.status_code==201,inserted.text
    assert inserted.json()["origin"]=="ci_generated_research_bundle"
    duplicate=cli.post(path+"/import",headers=root,json={})
    assert duplicate.json()["already_present"] is True
    result=cli.get("/api/research/epfl/candidates",headers=root).json()
    assert result["candidate_count"]==2
    assert result["review_source"]=="ci_generated_research_bundle"
    assert all("audit_gt_" not in str(x) for x in result["candidates"])
    with main.db() as c:
        assert c.execute("SELECT COUNT(*) FROM epfl_pair_candidates").fetchone()[0]==2
        assert c.execute("SELECT COUNT(*) FROM epfl_pair_reviews").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM synthetic_registry_runs").fetchone()[0]==0


def test_bundled_missing_does_not_fake_success(client_and_users,
                                                 tmp_path,monkeypatch):
    from app import epfl_review
    _,cli,root,_=client_and_users
    monkeypatch.setattr(epfl_review,"BUNDLED_CANDIDATES",tmp_path/"missing.json")
    a=cli.get("/api/research/epfl/bundled",headers=root)
    assert a.status_code==200 and a.json()["available"] is False
    b=cli.post("/api/research/epfl/bundled/import",headers=root,json={})
    assert b.status_code==404


def test_reject_wrong_video_and_nonmatching_geometry(client_and_users):
    _,cli,root,_=client_and_users
    raw=sample();raw["per_camera"]["0"]["sha256"]="0"*64
    assert cli.post("/api/research/epfl/import",headers=root,json=raw).status_code==422
    raw=sample();raw["frame_audits"][0]["candidate_evidence"][0]["calibration_distance"]=1000
    assert cli.post("/api/research/epfl/import",headers=root,json=raw).status_code==422
    raw=sample();raw["frame_audits"][0]["cross_camera_candidates"]=0
    assert cli.post("/api/research/epfl/import",headers=root,json=raw).status_code==422
    assert cli.get("/api/research/epfl/candidates",headers=root).json()["candidate_count"]==0
