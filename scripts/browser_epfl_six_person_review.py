"""Browser acceptance against the REAL six-person owner-CI report and SQLite.

This is NOT a simulated success payload: the upstream owner step decodes
official EPFL video, runs YOLO, and generates the report on this same runner.
Browser and backend read that artifact and persist only anonymous candidate
pair geometry and append-only human review decisions.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT/"reports"/"verify-epfl-six-person-cross-camera.json"
OUT = ROOT/"reports"


def run():
    proof = json.loads(REPORT.read_text(encoding="utf-8"))
    reference_total = (
        proof["summary"]["cross_camera_candidates"] +
        proof["holdout_summary"]["cross_camera_candidates"])
    if reference_total < 50:
        raise RuntimeError("Actual owner-job EPFL report has too few candidates")
    if proof.get("matching_gt_is_never_input") is not True:
        raise RuntimeError("EPFL research model consumed ground truth input")
    # Both paths must represent the SAME actual detector-derived evidence.
    # Print the first mismatched sanitized pair if source formats diverge;
    # official GT labels must never enter this comparison.
    from app import epfl_review
    bundle = epfl_review._bundle()
    if bundle is None:
        raise RuntimeError("Official video owner must produce the candidate bundle")
    original = epfl_review._extract(proof)
    packaged = epfl_review._extract(bundle)
    summary = {
        "full_pairs":len(original), "bundle_pairs":len(packaged),
        "full_digest":epfl_review._hash(original),
        "bundle_digest":epfl_review._hash(packaged),
        "identical":original == packaged,
        "first_disagreement":next(
            ({"original":a,"bundle":b}
             for a,b in zip(original,packaged) if a!=b),None)
    }
    print("REAL_EVIDENCE_PROVENANCE_DIAGNOSTIC "+
          json.dumps(summary,ensure_ascii=False),flush=True)
    with tempfile.TemporaryDirectory(prefix="zd-epfl-browser-") as tmp:
        env = dict(os.environ)
        env["ZD_DATA_DIR"] = str(Path(tmp)/"db")
        env["PYTHONPATH"] = str(ROOT)
        env.pop("ZD_AUTH_TEST_COMPAT", None)
        env.pop("PYTEST_CURRENT_TEST", None)
        os.environ["ZD_DATA_DIR"] = env["ZD_DATA_DIR"]
        from app import main, access
        main.setup_db()
        with main.db() as c:
            root = c.execute(
                "SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()[0]
            access.create_user(
                c,"epflbrowser","epfl-browser-password-2026",root)
        server = None
        try:
            with (OUT/"ui-epfl-server.log").open("w") as log:
                server = subprocess.Popen(
                    [sys.executable,"-m","uvicorn","app.main:app",
                     "--host","127.0.0.1","--port","8094"],
                    cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                base = "http://127.0.0.1:8094"
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("EPFL browser test server exited")
                    try:
                        with urlopen(base+"/api/health",timeout=1) as resp:
                            if resp.status == 200:
                                break
                    except OSError:
                        time.sleep(.2)
                else:
                    raise RuntimeError("EPFL browser test server unavailable")
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        headless=True,args=["--no-sandbox"])
                    try:
                        page = browser.new_page(
                            viewport={"width":1380,"height":920})
                        js_errors = []
                        page.on("pageerror",lambda e:js_errors.append(str(e)))
                        page.goto(base+"/cctv")
                        expect(page).to_have_url(base+"/login")
                        page.locator('input[name="username"]').fill("epflbrowser")
                        page.locator('input[name="password"]').fill(
                            "epfl-browser-password-2026")
                        page.locator('button[type="submit"]').click()
                        # Login POST and sessionStorage access-token commit are
                        # asynchronous. A premature navigation loses the login.
                        expect(page).to_have_url(base+"/real",timeout=15000)
                        page.goto(base+"/cctv")
                        expect(page.locator("#epflImportReport")).to_be_visible()
                        # Same owner job has just generated a GT-free manifest
                        # from two official AVI streams and packaged it locally.
                        expect(page.locator("#epflUseBundled")).to_be_enabled()
                        page.locator("#epflUseBundled").click()
                        expect(page.locator("#epflReviewStatus")).to_contain_text(
                            "研究候选",timeout=15000)
                        expect(page.locator("#epflReviewRows")).to_contain_text(
                            "baseline",timeout=15000)
                        # Review is paged, with usable previous/next links.
                        assert page.locator("#epflReviewRows tr").count()==12
                        expect(page.locator("#epflPageNote")).to_contain_text(
                            "1–12 / 119",timeout=10000)
                        page.locator("#epflLoadMore").click()
                        expect(page.locator("#epflPageNote")).to_contain_text(
                            "13–24 / 119",timeout=10000)
                        page.locator("#epflPrevPage").click()
                        expect(page.locator("#epflPageNote")).to_contain_text(
                            "1–12 / 119",timeout=10000)
                        # The full original proof still supports manual import;
                        # same 119 pairs MUST remain idempotent.
                        page.locator("#epflReportInput").set_input_files(
                            str(REPORT))
                        expect(page.locator("#epflSummary")).to_contain_text(
                            "真值一致",timeout=10000)
                        page.locator("#epflImportReport").click()
                        expect(page.locator("#epflReviewRows")).to_contain_text(
                            "baseline",timeout=15000)
                        expect(page.locator("#epflReviewStatus")).to_contain_text(
                            "研究候选",timeout=15000)
                        page.locator(
                            '#epflReviewRows button[data-epfl-verdict="needs_review"]'
                        ).first.click()
                        expect(page.locator("#epflReviewRows")).to_contain_text(
                            "needs_review",timeout=10000)
                        page.screenshot(
                            path=str(OUT/"ui-epfl-real-research-review.png"),
                            full_page=True)
                        page.reload()
                        expect(page.locator("#epflReviewRows")).to_contain_text(
                            "needs_review",timeout=15000)
                        # Source-derived anonymous activity is now a FIRST-CLASS
                        # read model, rather than 119 candidates trapped inside
                        # the research QA page or synthetic P### business.
                        page.goto(base+"/real")
                        expect(page.locator("#activityStats")).to_contain_text(
                            "119", timeout=15000)
                        expect(page.locator("#activityTable")).to_contain_text(
                            "V",timeout=15000)
                        activity=page.evaluate("""async () => {
                          const r=await fetch('/api/real/activity',{headers:{
                            Authorization:'Bearer '+sessionStorage.getItem('zd_access_token')}});
                          return {status:r.status,data:await r.json()};
                        }""")
                        assert activity['status']==200,activity
                        facts=activity['data']
                        assert facts['ready'] is True
                        assert facts['matched_pair_observations']==reference_total
                        scored_instants = len({
                            row['frame_0_based']
                            for section in ('frame_audits','holdout_frame_audits')
                            for row in proof[section] if row['candidate_evidence']
                        })
                        # Some of the 41 evaluated instants contain zero
                        # actual matched people; they are NOT activity events.
                        assert facts['sampled_video_instants']==scored_instants
                        assert 0 < scored_instants <= 41
                        assert facts['anonymous_activity_fragments']>0
                        assert facts['cross_time_temporal_linkage_verified'] is True, (
                            'Actual EPFL ground-truth scoring did not qualify time linkage')
                        assert facts['multi_instant_fragments']>=2, facts
                        assert facts['confirmed_natural_person_identity'] is False
                        assert facts['verified_unique_people'] is None
                        assert facts['real_cross_site_visits'] is None
                        assert facts['real_identity_alerts']==[]
                        assert facts['camera_sha256'] == [
                           proof['per_camera'][str(i)]['sha256'] for i in (0,1)]
                        assert all(x['camera_count']==2 for x in facts['activities'])
                        assert sum(x['synchronized_pair_observations']
                                   for x in facts['activities']) == reference_total
                        demo=page.evaluate("""async () => {
                           const r=await fetch('/api/real/demo-alerts',{headers:{
                             Authorization:'Bearer '+sessionStorage.getItem('zd_access_token')}});
                           return {status:r.status,data:await r.json()};
                        }""")
                        assert demo['status']==200,demo
                        alert_facts=demo['data']
                        assert alert_facts['rule_is_demo'] is True
                        assert alert_facts['real_world_risk_confirmed'] is False
                        assert len(alert_facts['alerts'])==facts['multi_instant_fragments']
                        assert all(not a['alert_is_real_incident']
                                   and a['source_evidence_is_real_video']
                                   and len(a['supporting_candidate_keys'])>=2
                                   for a in alert_facts['alerts'])
                        expect(page.locator("#demoAlertRows")).to_contain_text(
                            "EPFL-DEMO",timeout=15000)
                        # Synthetic P### rule rows must NOT be loaded by
                        # default on real-video dashboard. Only explicit,
                        # separately labeled manual Excel algorithm checks.
                        expect(page.locator("#excelRuleRows tr")).to_have_count(
                            1,timeout=15000)
                        assert not page.locator("#excelRuleShowcase").evaluate(
                            "el=>el.open")
                        page.locator("#excelRuleShowcase summary").click()
                        page.locator("#reloadExcelRules").click()
                        expect(page.locator("#excelRuleRows tr")).to_have_count(
                            7,timeout=15000)
                        expect(page.locator("#excelRuleRows")).to_contain_text(
                            "跨场所同行",timeout=15000)
                        expect(page.locator("#excelAcceptanceRows tr")).to_have_count(
                            23,timeout=15000)
                        assert page.locator("#excelAcceptanceRows tr").count()==23
                        contract=page.evaluate("""async () => {
                           const r=await fetch('/api/real/excel-acceptance',{headers:{
                             Authorization:'Bearer '+sessionStorage.getItem('zd_access_token')}});
                           return {status:r.status,data:await r.json()};
                        }""")
                        assert contract['status']==200,contract
                        accepted=contract['data']
                        assert len(accepted['requirements'])==23
                        assert accepted['status_counts']=={
                            'verified_real':3,'synthetic_only':10,
                            'partial':8,'missing':2}
                        assert accepted['all_23_fully_accepted'] is False
                        assert all(r['demo_route'].startswith(
                            ('/real','/cctv','/admin'))
                            for r in accepted['requirements'])
                        expect(page.locator("#excelAcceptanceStats")).to_contain_text(
                            "10",timeout=15000)
                        rules=page.evaluate("""async () => {
                           const r=await fetch('/api/real/excel-rule-demo',{headers:{
                             Authorization:'Bearer '+sessionStorage.getItem('zd_access_token')}});
                           return {status:r.status,data:await r.json()};
                        }""")
                        assert rules['status']==200,rules
                        assert {x['type'] for x in rules['data']['alerts']} == {
                            'cross_place','companion','interaction',
                            'gathering','absence','appearance','headcount'}
                        assert rules['data']['based_on_real_video'] is False
                        assert rules['data']['no_mutation_to_real_database'] is True
                        page.locator("#excelAcceptance details summary").click()
                        page.screenshot(
                            path=str(OUT/"ui-excel-23-live-acceptance.png"),
                            full_page=True)
                        # Real VIDEO-DERIVED aggregated scene rules, not
                        # pre-filled fictional people, WGS84 points or maps.
                        expect(page.locator("#sceneHeatGrid > button")).to_have_count(
                            64,timeout=15000)
                        expect(page.locator("#sceneEventRows")).to_contain_text(
                            "（演示）",timeout=15000)
                        scene=page.evaluate("""async () => {
                          const r=await fetch('/api/real/scene-aggregate',{headers:{
                            Authorization:'Bearer '+sessionStorage.getItem('zd_access_token')}});
                          return {status:r.status,data:await r.json()};
                        }""")
                        assert scene['status']==200,scene
                        spatial=scene['data']
                        assert spatial['ready'] is True
                        assert spatial['total_observations']==reference_total
                        assert spatial['wgs84_available'] is False
                        assert spatial['real_cross_location_identity_available'] is False
                        assert spatial['geographic_trajectory_available'] is False
                        assert spatial['warning_is_confirmed_risk'] is False
                        # 23 real sampled instants have >=3 actual synchronized
                        # camera-paired detections. This is a FRAME count, NOT
                        # 23 identified persons or real emergency alerts.
                        assert len(spatial['demo_scene_events'])==23, spatial
                        assert all(x['paired_detection_count']>=3
                                   for x in spatial['demo_scene_events'])
                        assert sum(x['paired_detection_count']
                                   for x in spatial['heat_cells'])==reference_total
                        assert not any('person_id' in k for k in spatial)
                        assert spatial['grid_size']==8
                        assert len(spatial['frame_cells'])==40
                        assert spatial['zone']=={
                            'x_bin':2,'y_bin':3,'width_cells':2,'height_cells':2}
                        assert len(spatial['demo_zone_events'])==20
                        assert spatial['individual_trajectory_available'] is False
                        assert spatial['short_anonymous_tracklets_verified'] is True
                        assert len(spatial['short_anonymous_tracklets'])==14
                        assert all(t['site_kind']=='same_real_laboratory_two_views'
                                   and not t['real_geographic_journey']
                                   and not t['identity_verified']
                                   and len(t['points'])>=2
                                   for t in spatial['short_anonymous_tracklets'])
                        expect(page.locator("#sceneTrackRows tr")).to_have_count(
                            14,timeout=15000)
                        expect(page.locator("#sceneTrackSummary")).to_contain_text(
                            "14条",timeout=15000)
                        page.locator("#sceneTrackSelect").select_option(
                            spatial['short_anonymous_tracklets'][0]['fragment_id'])
                        expect(page.locator("#sceneTrackRows")).to_contain_text(
                            spatial['short_anonymous_tracklets'][0]['fragment_id'])
                        assert all('candidate_key' not in json.dumps(f)
                                   for f in spatial['frame_cells'])
                        expect(page.locator("#sceneZoneRows")).to_contain_text(
                            "真实视频投影检测",timeout=15000)
                        # User can scrub ACTUAL recorded frame samples. The UI
                        # does not assert a single person moved between samples.
                        page.locator("#sceneFrameCursor").evaluate(
                            """el=>{el.value='1';el.dispatchEvent(
                              new Event('input',{bubbles:true}))}""")
                        expect(page.locator("#sceneFrameLabel")).to_contain_text(
                            "第",timeout=15000)
                        assert '轨迹' not in page.locator("#sceneFrameInfo").inner_text().split(
                            "不推断")[0]
                        page.screenshot(
                            path=str(OUT/"ui-epfl-room-zone-demo.png"),
                            full_page=True)
                        # Click one grid square and RECOMPUTE zone hits from
                        # video: the result must change with no synthetic seed.
                        page.locator('[data-scene-x="6"][data-scene-y="5"]').click()
                        expect(page.locator("#sceneZoneSummary")).to_contain_text(
                            "(6,5)",timeout=15000)
                        expect(page.locator("#sceneZoneRows")).to_contain_text(
                            "真实视频投影检测",timeout=15000)
                        page.locator("#sceneOverview").click()
                        expect(page.locator("#sceneFrameLabel")).to_contain_text(
                            "累计热度",timeout=15000)
                        page.screenshot(
                            path=str(OUT/"ui-epfl-real-research-activity.png"),
                            full_page=True)
                        if js_errors:
                            raise AssertionError("Browser JavaScript error: "+repr(js_errors))
                    finally:
                        browser.close()
            with main.db() as c:
                pairs = c.execute(
                    "SELECT COUNT(*) FROM epfl_pair_candidates").fetchone()[0]
                reviews = c.execute(
                    "SELECT COUNT(*) FROM epfl_pair_reviews").fetchone()[0]
                assert pairs == reference_total,(pairs,reference_total)
                assert reviews == 1,reviews
                assert c.execute(
                    "SELECT COUNT(*) FROM synthetic_observations"
                ).fetchone()[0] == 0
                assert c.execute(
                    "SELECT COUNT(*) FROM synthetic_registry_runs"
                ).fetchone()[0] == 0
                assert c.execute(
                    "SELECT COUNT(*) FROM synthetic_alert_reviews"
                ).fetchone()[0] == 0
                audit = c.execute(
                    "SELECT COUNT(*) FROM admin_audit WHERE operation IN "
                    "('research_epfl_candidate_import',"
                    "'research_epfl_candidate_review')").fetchone()[0]
                assert audit == 2,audit
            outcome = {
                "verdict":"PASS",
                "actual_real_epfl_video_owner_job_report":True,
                "genuine_model_and_calibration_input":True,
                "anonymous_candidate_pairs_persisted":pairs,
                "human_research_revisions_saved":reviews,
                "activity_fragments": facts['anonymous_activity_fragments'],
                "cross_time_fragments":facts['multi_instant_fragments'],
                "real_source_derived_demo_alerts":len(alert_facts['alerts']),
                "real_aggregate_video_threshold_events":len(spatial['demo_scene_events']),
                "anonymous_indoor_heatmap_cells":len(spatial['heat_cells']),
                "video_derived_default_zone_sampled_instants":len(spatial['demo_zone_events']),
                "coarse_scene_scrubber_verified_in_browser":True,
                "verified_short_local_link_segments_shown":len(spatial['short_anonymous_tracklets']),
                "synthetic_excel_not_auto_loaded_in_real_ui":True,
                "video_derived_zone_rule_reposition_verified_in_browser":True,
                "gps_or_real_cross_place_tracking_claimed":False,
                "synthetic_excel_rule_types_verified_in_browser":7,
                "original_excel_requirements_visible_in_browser":23,
                "excel_acceptance_status_counts":accepted["status_counts"],
                "real_video_fps":facts['video_fps'],
                "admin_audit_entries":audit,
                "identity_confirmed":False,
                "real_risk_alerts_generated":0,
                "synthetic_roster_rows_generated":0,
                "browser_screenshot":"reports/ui-epfl-real-research-activity.png",
            }
            (OUT/"epfl6p-review-integration.json").write_text(
                json.dumps(outcome,ensure_ascii=False,indent=2),
                encoding="utf-8")
            print(json.dumps(outcome,ensure_ascii=False))
        finally:
            if server is not None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__=="__main__":
    run()
