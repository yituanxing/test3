"""The original Excel acceptance UI must not turn synthetic data into real claims."""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def actor_clients(tmp_path,monkeypatch):
    from app import main, access
    monkeypatch.delenv("ZD_AUTH_TEST_COMPAT",raising=False)
    monkeypatch.setattr(main,"DB",tmp_path/"excel-v1.sqlite")
    main.setup_db()
    with main.db() as c:
        state=c.execute("SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()[0]
        county=c.execute("SELECT id FROM regions WHERE level=2 LIMIT 1").fetchone()[0]
        access.create_user(c,"exceladmin","excel-admin-test-password",state)
        access.create_user(c,"excelcounty","excel-county-test-password",county)
    cli=TestClient(main.app)
    def auth(u,p):
        r=cli.post("/api/auth/login",json={"username":u,"password":p})
        assert r.status_code==200,r.text
        return {"Authorization":"Bearer "+r.json()["access_token"]}
    return main,cli,auth("exceladmin","excel-admin-test-password"),auth(
        "excelcounty","excel-county-test-password")


def test_exact_23_from_manifest_and_admin_not_other_roles(actor_clients):
    main,cli,root,county=actor_clients
    url="/api/demo/excel-acceptance"
    assert cli.get(url).status_code==401
    assert cli.get(url,headers=county).status_code==403
    r=cli.get(url,headers=root)
    assert r.status_code==200,r.text
    value=r.json()
    assert value["counts"]=={"verified_real":3,"synthetic_only":10,
                              "partial":8,"missing":2}
    assert value["all_23_verified_real"] is False
    assert [x["id"] for x in value["requirements"]]==list(range(1,24))
    assert all(x["demo_url"].startswith(('/portal','/cctv','/monitor',
        '/admin','/base','/business','/insights'))
        for x in value["requirements"])
    manifest=json.loads((Path(__file__).parents[1]/"config"/
         "excel_acceptance_v1.json").read_text(encoding="utf-8"))
    for shown,raw in zip(value["requirements"],manifest["requirements"]):
        assert shown["status"]==raw["status"]
        assert shown["verified_scope"]==raw["verified_scope"]
    mode={"verified_real":"original_scope",
          "synthetic_only":"synthetic_business",
          "partial":"limited_demonstration",
          "missing":"not_replaced"}
    keys=("delivered_now","alternative_used","why_demo_usable",
          "not_equivalent_to","future_implementation","acceptance_gate")
    for item in value["requirements"]:
        assert item["delivery_mode"]==mode[item["status"]]
        assert all(isinstance(item[k],str) and len(item[k])>=18 for k in keys)
        assert item["next_priority"] in ("P0","P1","P2")
        if item["status"]=="missing":
            assert "没有有效替代" in item["alternative_used"]
    assert value["delivery_assessment_date"]=="2026-10-14"
    assert value["requirements"][13]["status"]=="missing" # #14
    assert value["requirements"][16]["status"]=="missing" # #17


def test_epfl_review_summary_tracks_latest_review_not_people(actor_clients):
    from app import epfl_review
    main,cli,root,county=actor_clients
    endpoint="/api/research/epfl/summary"
    assert cli.get(endpoint).status_code==401
    assert cli.get(endpoint,headers=county).status_code==403
    empty=cli.get(endpoint,headers=root).json()
    assert empty["candidate_count"]==0 and empty["has_report"] is False
    assert empty["feeds_real_alerts"] is False
    from tests.test_epfl_review import sample
    report=sample()
    imported=cli.post("/api/research/epfl/import",json=report,headers=root)
    assert imported.status_code==201,imported.text
    summary=cli.get(endpoint,headers=root).json()
    assert summary["candidate_count"]==2
    assert summary["pending_count"]==2
    assert summary["reviewed_count"]==0
    keys=cli.get("/api/research/epfl/candidates",headers=root).json()["candidates"]
    key=keys[0]["candidate_key"]
    for verdict in ("plausible_same_target","false_match"):
        decided=cli.post("/api/research/epfl/reviews",headers=root,json={
            "candidate_key":key,"verdict":verdict,
            "reason":"复核同步原片与检测结果后的人工研究结论"})
        assert decided.status_code==201,decided.text
    final=cli.get(endpoint,headers=root).json()
    assert final["candidate_count"]==2
    assert final["pending_count"]==1
    assert final["reviewed_count"]==1
    assert final["false_match_count"]==1
    assert final["plausible_count"]==0
    assert final["verified_unique_real_people"] is False
    with main.db() as c:
        assert c.execute("SELECT COUNT(*) FROM epfl_pair_reviews").fetchone()[0]==2
        assert c.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM synthetic_alert_reviews").fetchone()[0]==0
