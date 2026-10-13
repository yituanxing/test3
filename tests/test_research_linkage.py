"""Excel critical identity boundary acceptance.

Not automatic face recognition. An administrator manually associates
pseudonymous camera-local evidence for a CONTROLLED research demonstration,
after accepting local-track QA and verified event-time camera-site epochs.
The resulting sessions must never enter real alerts or synthetic P### roster.
"""
from datetime import datetime, timezone
import time
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def system(tmp_path,monkeypatch):
    from app import main,access
    monkeypatch.delenv("ZD_AUTH_TEST_COMPAT",raising=False)
    monkeypatch.setattr(main,"DB",tmp_path/"study-links.sqlite")
    main.setup_db()
    with main.db() as c:
        root=c.execute("SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()["id"]
        county=c.execute("SELECT id FROM regions WHERE level=2 LIMIT 1").fetchone()["id"]
        a=c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()["id"]
        b=c.execute("SELECT id FROM places WHERE name='测试场所 B'").fetchone()["id"]
        access.create_user(c,"linkadmin","link-admin-password-2026",root)
        access.create_user(c,"linkcounty","link-county-password-2026",county)
    cli=TestClient(main.app)
    def token(user,pw):
        r=cli.post("/api/auth/login",json={"username":user,"password":pw})
        assert r.status_code==200,r.text
        return {"Authorization":"Bearer "+r.json()["access_token"]}
    return main,cli,a,b,token("linkadmin","link-admin-password-2026"),token(
        "linkcounty","link-county-password-2026")


def _sync_persons(cli,root,places,with_third=False):
    for camera,place in places.items():
        response=cli.post("/api/frigate/cameras",headers=root,
                          json={"camera":camera,"place_id":place})
        assert response.status_code==200,response.text
    time.sleep(.025)
    started=time.time()
    tracks=[{"id":"same-local-1","camera":camera,"label":"person",
             "start_time":started+index*3,"false_positive":False,
             "data":{"top_score":0.8},"has_snapshot":True}
            for index,camera in enumerate(places)]
    fake=Mock()
    fake.raise_for_status.return_value=None
    fake.json.return_value=tracks
    with patch("app.main.httpx.get",return_value=fake):
        res=cli.post("/api/frigate/sync",headers=root)
    assert res.status_code==200,res.text
    assert res.json()["inserted"]==len(tracks)
    return tracks


def _qa(cli,root,key):
    result=cli.post(f"/api/site/occurrences/{key}/quality",headers=root,
                    json={"verdict":"valid_local_target",
                          "reason":"依据受控样本原始摄像机目标审核通过"})
    assert result.status_code==201,result.text
    return result


def _link(cli,root,key,alias="D001",action="attach"):
    return cli.post("/api/research/links",headers=root,json={
        "occurrence_key":key,"alias":alias,"action":action,
        "reason":"受控研究素材人工目视确认，仅用于匿名演示会话分组"})


def test_two_camera_same_site_manual_link_creates_one_research_session(system):
    main,cli,a,b,root,county=system
    _sync_persons(cli,root,{"door_left":a,"door_right":a})
    rows=cli.get("/api/site/occurrences",headers=root).json()["occurrences"]
    assert len(rows)==2
    assert len({r["occurrence_key"] for r in rows})==2
    assert {r["track_id"] for r in rows}=={"same-local-1"}
    assert all(r["binding_epoch_id"] is not None for r in rows)
    assert cli.get("/api/research/links").status_code==401
    assert cli.get("/api/research/links",headers=county).status_code==403
    assert cli.get("/api/research/visits",headers=county).status_code==403
    assert cli.post("/api/research/links",headers=county,json={
        "occurrence_key":rows[0]["occurrence_key"],"alias":"D001","action":"attach",
        "reason":"不能允许县级随意推断同一真人身份"}).status_code==403
    rejected=_link(cli,root,rows[0]["occurrence_key"])
    assert rejected.status_code==409
    for r in rows:
        _qa(cli,root,r["occurrence_key"])
        linked=_link(cli,root,r["occurrence_key"])
        assert linked.status_code==201,linked.text
        assert linked.json()["effective_status"]=="active_manual_assertion"
        assert linked.json()["identity_verified"] is False
        assert linked.json()["eligible_for_real_alerts"] is False
    index=cli.get("/api/research/links",headers=root).json()
    assert len(index["links"])==2
    assert index["active"]==2
    visits=cli.get("/api/research/visits?gap_minutes=20",headers=root)
    assert visits.status_code==200,visits.text
    result=visits.json()
    assert result["mode"]=="manual_research_candidate_visits"
    assert result["feeds_synthetic_P_roster"] is False
    assert result["counts_as_verified_site_visits"] is False
    assert result["not_unique_real_people"] is True
    assert len(result["visits"])==1
    candidate=result["visits"][0]
    assert candidate["research_alias"]=="D001"
    assert candidate["place_id"]==a
    assert candidate["camera_count"]==2
    assert candidate["local_occurrence_count"]==2
    assert set(candidate["evidence_occurrence_keys"])=={
        x["occurrence_key"] for x in rows}
    with main.db() as c:
        assert c.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM synthetic_registry_runs").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM research_occurrence_links").fetchone()[0]==2
        assert c.execute("""SELECT COUNT(*) FROM admin_audit
             WHERE operation='research_manual_link'""").fetchone()[0]==2
    assert cli.get("/api/business/analysis",headers=root).json()["person_count"]==0

    # A detector revision invalidates the manual association, without
    # editing historic source events or silently carrying over the same label.
    with main.db() as c:
        c.execute("""UPDATE frigate_object_events SET score=.99,updated_at=?
           WHERE camera='door_left' AND event_id='same-local-1'""",
           (datetime.now(timezone.utc).isoformat(),))
    changed=cli.get("/api/research/links",headers=root).json()
    assert changed["active"]==1
    assert {x["evidence_status"] for x in changed["links"]}=={
        "active_manual_assertion","quality_not_accepted"}
    after=cli.get("/api/research/visits",headers=root).json()
    assert len(after["visits"])==1 and after["visits"][0]["camera_count"]==1
    assert after["excluded_links"]==1
    _qa(cli,root,rows[0]["occurrence_key"])
    # Even refreshed QA must not implicitly reactivate OLD asserted links.
    reevaluated=cli.get("/api/research/links",headers=root).json()
    assert reevaluated["active"]==1
    assert {x["evidence_status"] for x in reevaluated["links"]}=={
        "active_manual_assertion","stale_evidence"}
    renewed=_link(cli,root,rows[0]["occurrence_key"])
    assert renewed.status_code==201
    assert cli.get("/api/research/visits",headers=root).json()["visits"][0][
        "camera_count"]==2

    revoked=_link(cli,root,rows[1]["occurrence_key"],action="detach")
    assert revoked.status_code==201,revoked.text
    assert revoked.json()["effective_status"]=="detached"
    assert cli.get("/api/research/visits",headers=root).json()["visits"][0][
        "camera_count"]==1
    with main.db() as c:
        assert c.execute("SELECT COUNT(*) FROM research_occurrence_links").fetchone()[0]==4
        assert c.execute("SELECT COUNT(*) FROM frigate_object_events").fetchone()[0]==2
    main.setup_db()
    assert cli.get("/api/research/links",headers=root).json()["active"]==1


def test_two_distinct_sites_produce_two_candidate_visits_without_real_alerts(system):
    main,cli,a,b,root,county=system
    _sync_persons(cli,root,{"frontA":a,"frontB":b})
    rows=cli.get("/api/site/occurrences",headers=root).json()["occurrences"]
    for item in rows:
        _qa(cli,root,item["occurrence_key"])
        assert _link(cli,root,item["occurrence_key"]).status_code==201
    visits=cli.get("/api/research/visits",headers=root).json()
    assert {x["place_id"] for x in visits["visits"]}=={a,b}
    assert len(visits["visits"])==2
    assert all(x["real_person_identity_verified"] is False
               and x["eligible_for_real_alerts"] is False
               and x["eligible_for_roster"] is False for x in visits["visits"])
    assert cli.get("/api/business/analysis",headers=root).json()["alerts"]==[]
    assert cli.get("/api/business/analysis",headers=root).json()["person_count"]==0
    # A cross-site visit requires a confirmed real-person match to activate
    # real cross-site alerts; an operator-asserted label is NOT sufficient.


def test_bad_source_no_epoch_and_bad_alias_are_rejected(system):
    main,cli,a,b,root,county=system
    # Legacy events have an unknown epoch and are NOT safe as linked visits.
    with main.db() as c:
        c.execute("""INSERT INTO frigate_camera_bindings(camera,place_id)
                     VALUES('old_pseudo_cam',?)""",(a,))
        c.execute("""INSERT INTO frigate_object_events
          (event_id,camera,place_id,label,started_at,score,has_snapshot,updated_at)
          VALUES('old-evt','old_pseudo_cam',?,'person',
                 '2024-02-02T01:00:00+00:00',.88,0,'2024-02-02T01:01:00+00:00')""",(a,))
    item=next(x for x in cli.get("/api/site/occurrences",headers=root).json()[
        "occurrences"] if x["track_id"]=="old-evt")
    assert item["binding_epoch_id"] is None
    _qa(cli,root,item["occurrence_key"])
    assert _link(cli,root,item["occurrence_key"]).status_code==409
    assert cli.post("/api/research/links",headers=root,json={
        "occurrence_key":item["occurrence_key"],"alias":"P001","action":"attach",
        "reason":"错误地直接混用合成身份命名空间"}).status_code==422
    assert cli.post("/api/research/links",headers=root,json={
        "occurrence_key":"f"*24,"alias":"D001","action":"attach",
        "reason":"不存在真实来源的局部目标"}).status_code==404
