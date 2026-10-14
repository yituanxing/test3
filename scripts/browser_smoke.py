"""Chromium smoke test for public-facing layout, login and page navigation.

Runs against a local server and a temporary SQLite database. No Frigate,
person identification or live camera data is required or collected.
"""
import os
import sys
import json
import time
import tempfile
import subprocess
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports"
OUT.mkdir(exist_ok=True)
checks = []

def run():
    server = None
    with tempfile.TemporaryDirectory(prefix="zd-ui-smoke-") as temp:
        env = dict(os.environ)
        env["ZD_DATA_DIR"] = str(Path(temp) / "database")
        env["PYTHONPATH"] = str(ROOT)
        env.pop("ZD_AUTH_TEST_COMPAT", None)
        env.pop("PYTEST_CURRENT_TEST", None)
        os.environ["ZD_DATA_DIR"] = env["ZD_DATA_DIR"]
        from app import main, access
        main.setup_db()
        password = "temporary-browser-test-password"
        with main.db() as c:
            region = c.execute("SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()[0]
            access.create_user(c, "uismoke", password, region)
            # The browser will invoke the real local video decoder and Haar
            # classifier, then adjudicate a stored anonymous detection box.
            face_source_id = c.execute(
                "INSERT INTO sources(place_id,name,kind,uri) VALUES(?,?,?,?)",
                (1, "browser-real-face-review", "file",
                 str(ROOT / "samples" / "astronaut-face-test.mp4"))).lastrowid
            # Deliberately offline RTSP endpoint for capture UI start/stop
            # contract testing. We do NOT pretend any decoded media exists.
            rtsp_source_id = c.execute(
                "INSERT INTO sources(place_id,name,kind,uri) VALUES(?,?,?,?)",
                (1, "browser-offline-RTSP-contract", "rtsp",
                 "rtsp://127.0.0.1:1/never-online")).lastrowid
        try:
            with (OUT / "ui-server.log").open("w") as log:
                server = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "app.main:app",
                     "--host", "127.0.0.1", "--port", "8093"],
                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                base = "http://127.0.0.1:8093"
                for attempt in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("Server terminated")
                    try:
                        with urlopen(base + "/api/health", timeout=1) as reply:
                            if reply.status == 200:
                                break
                    except OSError:
                        time.sleep(0.3)
                else:
                    raise RuntimeError("Server did not start")
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                    try:
                        page = browser.new_page(viewport={"width": 1365, "height": 900})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(base + "/portal")
                        expect(page).to_have_url(base + "/login")
                        checks.append("unauthenticated_redirect")
                        page.locator('input[name="username"]').fill("uismoke")
                        page.locator('input[name="password"]').fill(password)
                        page.locator('button[type="submit"]').click()
                        expect(page).to_have_url(base + "/portal", timeout=10000)
                        expect(page.locator("h2.lead")).to_contain_text("辖区重点场所运行概览")
                        checks.append("login_and_dashboard")
                        page.screenshot(path=str(OUT / "ui-desktop.png"), full_page=True)
                        page.locator('a[data-tab="places"]').click()
                        expect(page.locator("h2.lead")).to_contain_text("辖区场所列表")
                        checks.append("places_navigation")
                        page.screenshot(path=str(OUT / "ui-places.png"), full_page=True)
                        page.locator('a[data-tab="overview"]').click()
                        expect(page.locator("h2.lead")).to_contain_text("辖区重点场所运行概览")
                        checks.append("home_navigation")
                        # Primary human CCTV route: real-source catalog and
                        # truthful empty state. A real remote pair is verified
                        # separately in the cctv-real labelled CI step.
                        page.goto(base+"/cctv")
                        expect(page.get_by_role("heading",name="真实监控与人体关联 · 验收工作台")).to_be_visible()
                        expect(page.locator("#place")).to_contain_text("测试场所 A")
                        expect(page.locator("#sources")).to_contain_text(
                            "尚无研究监控视频", timeout=12000)
                        assert page.locator("#sources tr").count()==1
                        expect(page.locator("#occurrenceRows")).to_contain_text(
                            "不会生成模拟数",timeout=12000)
                        expect(page.locator("#occurrenceNote")).to_contain_text(
                            "不合并跨机位轨迹",timeout=12000)
                        checks.append("real_fixed_camera_catalog_truthful_no_video_state")
                        checks.append("common_anonymous_local_occurrences_zero_state_browser")
                        page.screenshot(path=str(OUT/"ui-cctv-primary.png"),full_page=True)
                        # Excel #20/#23: the admin screen MUST actually attach
                        # Bearer auth, list real inventory, register an RTSP
                        # source and create a scope-bound login through its UI.
                        page.goto(base + "/admin")
                        expect(page.locator("#regionCount")).not_to_have_text("—")
                        expect(page.locator("#accountPanel")).to_be_visible()
                        expect(page.locator("#placesBody")).to_contain_text("测试场所 A")
                        page.locator("#newSourceName").fill("browser-added-RTSP")
                        page.locator("#newSourceUri").fill("rtsp://127.0.0.1:1/new-offline")
                        page.get_by_role("button", name="新增 RTSP").click()
                        expect(page.locator("#sourcesBody")).to_contain_text("browser-added-RTSP")
                        checks.append("authenticated_admin_rtsp_creation")
                        page.locator("#accountName").fill("smokecommunity")
                        page.locator("#accountPassword").fill("browser-account-strong-password")
                        page.locator("#accountNewRegion").select_option(
                            label="演示州 / 演示县市 / 演示街道 / 演示社区")
                        page.get_by_role("button", name="新建账号").click()
                        expect(page.locator("#accountsBody")).to_contain_text("smokecommunity")
                        checks.append("admin_four_tier_account_creation")
                        # Same school supports two cameras and one zone; real
                        # administration writes actual SQLite binding history.
                        expect(page.get_by_role("heading",name="公共场所—内部场景—摄像机关系")).to_be_visible()
                        page.locator("#sitePlace").select_option(label="测试场所 A")
                        page.locator("#siteZoneName").fill("学校西校门")
                        page.locator("#siteCategory").select_option("entrance")
                        page.get_by_role("button", name="创建场景区域").click()
                        expect(page.locator("#siteZonesRows")).to_contain_text(
                            "学校西校门",timeout=10000)
                        page.locator("#siteSource").select_option(label="browser-added-RTSP（测试场所 A）")
                        page.locator("#siteZone").select_option(label="学校西校门")
                        page.get_by_role("button",name="确认设备区域").click()
                        expect(page.locator("#siteHistory")).to_contain_text("学校西校门",timeout=12000)
                        page.reload()
                        expect(page.locator("#siteZonesRows")).to_contain_text("学校西校门",timeout=12000)
                        checks.append("real_browser_canonical_place_zone_camera_binding_persists")
                        page.screenshot(path=str(OUT / "ui-shared-site-zone-binding.png"), full_page=True)
                        page.screenshot(path=str(OUT / "ui-admin-operations.png"), full_page=True)

                        # Business-critical browser acceptance: actual server DB
                        # recomputes the truth case; this is not a UI mock.
                        page.goto(base+"/base")
                        expect(page.get_by_role("heading",name="场所人员底库 · 生成与人工矫正")).to_be_visible()
                        page.locator("#dedupDemo").click()
                        expect(page.locator("#toast")).to_contain_text("100 条原始抓拍")
                        page.get_by_role("button",name="② 生成底库草稿").click()
                        p099=page.locator("#candidateRows tr").filter(has_text="P099")
                        expect(p099).to_contain_text("100 条原始观测")
                        expect(p099).to_contain_text("1 次有效出现")
                        expect(p099).to_contain_text("3 次有效出现")
                        expect(p099.locator("td").nth(2)).to_contain_text("测试场所 B")
                        checks.append("actual_dedup_100_to_1_and_frequency_home_B")
                        page.screenshot(path=str(OUT/"ui-dedup-roster.png"),full_page=True)
                        # Playwright accept() defaults prompt text to the
                        # empty string, even when the webpage supplied a
                        # nonempty default. The actual review form requires a
                        # reason (>=2 chars); emulate a real user submitting it.
                        page.on("dialog",lambda dialog:(
                            dialog.accept("浏览器实际人工审核理由")
                            if dialog.type=="prompt" else dialog.accept()))
                        page.locator("#pub").click()
                        expect(page.locator("#kRunState")).to_contain_text("当前已发布底库")
                        page.locator("#strategy").select_option("earliest")
                        page.get_by_role("button",name="② 生成底库草稿").click()
                        expect(p099.locator("td").nth(2)).to_contain_text("测试场所 A")
                        expect(page.locator("#diffRows")).to_contain_text("P099")
                        expect(page.locator("#diffRows")).to_contain_text("迁移")
                        checks.append("actual_roster_version_change_and_evidence_diff")
                        page.screenshot(path=str(OUT/"ui-roster-v2-diff.png"),full_page=True)
                        # Requirement 3, browser-level acceptance: select a
                        # future cutoff to show that P099 truly leaves the new
                        # candidate roster without rewriting the published V1.
                        page.locator("#asOf").fill("2035-01-01T00:00")
                        page.get_by_role("button",name="② 生成底库草稿").click()
                        expect(page.locator("#kPeople")).to_have_text("0", timeout=12000)
                        expect(page.locator("#diffRows")).to_contain_text("P099")
                        expect(page.locator("#diffRows")).to_contain_text("移除")
                        expect(page.locator("#windowInfo")).to_contain_text("2035")
                        # A pending empty draft does not auto-publish itself.
                        expect(page.locator("#kRunState")).to_contain_text("未发布草稿")
                        checks.append("real_browser_rolling_window_removes_absent_fictional_person")
                        page.screenshot(path=str(OUT/"ui-roster-expiry-diff.png"),full_page=True)
                        # Excel #4/#5/#19: one analytics contract drives
                        # comparisons, rankings and scoped cross-home flows.
                        page.goto(base + "/insights")
                        expect(page.locator("#periodComparison")).to_contain_text(
                            "当期到访", timeout=12000)
                        expect(page.locator("#comparisonWindow")).to_contain_text(
                            "UTC", timeout=12000)
                        expect(page.locator("#placeBreakdown tr").first).to_be_visible()
                        expect(page.locator("#flowSummary")).to_contain_text("流入")
                        checks.append("shared_analytics_period_rank_flow_browser")
                        page.screenshot(path=str(OUT / "ui-shared-analytics.png"), full_page=True)
                        # Excel #7/#11: one rule config drives repeated N-person
                        # group matching and selected synthetic place/person lists.
                        page.goto(base + "/business")
                        expect(page.locator("#cfg_companion_min_people")).to_have_value("2", timeout=12000)
                        page.locator("#cfg_companion_min_people").fill("3")
                        page.locator("#watchMultiPlaces").select_option(label="测试场所 A")
                        page.locator("#watchPeople").fill("P099")
                        page.get_by_role("button",name="保存并重新分析").click()
                        expect(page.locator("#cfg_companion_min_people")).to_have_value("3", timeout=12000)
                        expect(page.locator("#watchPeople")).to_have_value("P099")
                        rule_config=page.evaluate("""async()=> (await
                            (await fetch('/api/business/config')).json())""")
                        assert rule_config['companion_min_people']==3,rule_config
                        assert rule_config['watch_person_ids']==['P099'],rule_config
                        assert len(rule_config['watch_place_ids'])==1,rule_config
                        checks.append("common_rule_n_person_and_multi_target_browser")
                        page.screenshot(path=str(OUT/"ui-shared-rules.png"),full_page=True)
                        # Excel #22: true browser CRUD with one shared rule
                        # instance engine; verify DB persistence across reload.
                        page.locator("#instType").select_option("appearance")
                        page.locator("#instName").fill("动画场景合成出现规则")
                        page.locator("#instPlaces").select_option(label="测试场所 B")
                        page.locator("#instPeople").fill("P099")
                        page.locator("#instSave").click()
                        expect(page.locator("#instanceRows")).to_contain_text(
                            "动画场景合成出现规则", timeout=12000)
                        page.reload()
                        expect(page.locator("#instanceRows")).to_contain_text(
                            "动画场景合成出现规则", timeout=12000)
                        first_row=page.locator("#instanceRows tr").filter(
                            has_text="动画场景合成出现规则")
                        first_row.get_by_role("button",name="暂停").click()
                        expect(page.locator("#instanceRows")).to_contain_text("暂停")
                        # Paused instance must suppress the default appearance
                        # singleton, NOT silently restore legacy events.
                        resp=page.evaluate("""async()=> (await
                            (await fetch('/api/business/analysis')).json())""")
                        assert not [a for a in resp['alerts']
                                    if a['type']=='appearance'],resp['rule_counts']
                        page.screenshot(path=str(OUT/"ui-rule-instances.png"),full_page=True)
                        checks.append("shared_rule_instance_browser_create_pause_persist")
                        # Real video and actual browser interaction: never rely
                        # on a stubbed API or synthetic detector boxes.
                        page.goto(base+"/monitor")
                        # The GB28181 status must be truthful even without SIP
                        # hardware or a WVP upstream: no simulated online device.
                        expect(page.locator("#gbStatus")).to_contain_text("尚未配置")
                        expect(page.locator("#gbPanel")).to_contain_text(
                            "WVP-PRO + ZLMediaKit")
                        checks.append("gb28181_not_configured_shown_as_unverified")
                        # Actual Chromium click path for a registered local
                        # RTSP source. The RTSP address intentionally has NO
                        # broadcaster: verify start/stop workflow and truthful
                        # failure state, not nonexistent video/identity results.
                        expect(page.locator("#captureSource option").filter(
                            has_text="browser-offline-RTSP-contract")).to_have_count(
                                1, timeout=12000)
                        page.locator("#captureSource").select_option(str(rtsp_source_id))
                        page.locator("#captureStart").click()
                        expect(page.locator("#captureJobs")).to_contain_text(
                            "已启用", timeout=12000)
                        with main.db() as check:
                            state = check.execute(
                                "SELECT desired_enabled FROM capture_jobs WHERE source_id=?",
                                (rtsp_source_id,)).fetchone()
                            assert state and state[0] == 1
                            assert check.execute(
                                "SELECT COUNT(*) FROM samples WHERE source_id=?",
                                (rtsp_source_id,)).fetchone()[0] == 0
                        page.locator("#captureJobs").get_by_role(
                            "button", name="停止任务").click()
                        expect(page.locator("#captureJobs")).to_contain_text(
                            "已停用", timeout=12000)
                        with main.db() as check:
                            assert check.execute(
                                "SELECT desired_enabled FROM capture_jobs WHERE source_id=?",
                                (rtsp_source_id,)).fetchone()[0] == 0
                        checks.append("chromium_rtsp_capture_start_stop_without_false_media")
                        page.screenshot(path=str(OUT/"ui-capture-start-stop.png"),
                                        full_page=True)
                        actual = page.evaluate("""async sourceId => {
                            const r=await fetch('/api/sources/'+sourceId+
                              '/process?max_frames=10&sample_every=5',
                              {method:'POST'});
                            return {status:r.status,body:await r.json()};
                        }""", face_source_id)
                        assert actual['status']==200, actual
                        assert actual['body']['face_detections']>0
                        expect(page.locator("#faceQualityTotals")).to_contain_text(
                            "检测框",timeout=12000)
                        page.reload()
                        expect(page.get_by_role("button",name="标记误检").first).to_be_visible(
                            timeout=12000)
                        page.get_by_role("button",name="标记误检").first.click()
                        expect(page.locator("#faceQualityTotals")).to_contain_text(
                            "人工标记误检 1 个",timeout=12000)
                        page.screenshot(path=str(OUT/"ui-anonymous-face-review.png"),
                                        full_page=True)
                        page.get_by_role("button",name="恢复待复核").first.click()
                        expect(page.locator("#faceQualityTotals")).to_contain_text(
                            "人工标记误检 0 个",timeout=12000)
                        with main.db() as check:
                            assert check.execute(
                                'SELECT COUNT(*) FROM face_box_reviews').fetchone()[0] == 2
                        checks.append("real_video_face_box_append_only_review_ui")
                        # Excel #13/#16: actual recorded MP4 -> model face boxes ->
                        # jurisdiction-scoped, source-filtered evidence ->
                        # on-demand redacted original frame. This is NOT
                        # biometric image similarity or independent-person ID.
                        page.locator("#evidenceSource").select_option(str(face_source_id))
                        page.locator("#evidenceWithBoxes").check()
                        page.locator("#evidenceSearchButton").click()
                        expect(page.locator("#evidencePageInfo")).to_contain_text(
                            "匹配",timeout=12000)
                        expect(page.locator("#faceEvidence")).to_contain_text(
                            "browser-real-face-review",timeout=12000)
                        page.locator("#faceEvidence button").last.click()
                        expect(page.locator("#redactedEvidence")).to_be_visible(
                            timeout=12000)
                        assert page.locator("#redactedEvidence").get_attribute("src").startswith("blob:")
                        checks.append("real_capture_evidence_query_scoped_and_redacted_preview_browser")
                        page.screenshot(path=str(OUT/"ui-video-capture-evidence-search.png"),
                                        full_page=True)
                        # Anime scene map is a STORYBOARD, not GPS. Open a real
                        # decoded file and accept one candidate manually.
                        page.goto(base+"/anime-scenes")
                        expect(page.get_by_role("heading",name="动画场景 · 时间轴 · 示意图")).to_be_visible()
                        # Actual browser file upload (not a fake API or a mock
                        # path): verify the raw bytes are registered as a source.
                        page.locator("#uploadFile").set_input_files(
                            str(ROOT/"samples"/"astronaut-face-test.mp4"))
                        page.locator("#upload").click()
                        expect(page.locator("#status")).to_contain_text(
                            "视频上传成功",timeout=20000)
                        expect(page.locator("#source")).to_contain_text("上传动画")
                        checks.append("actual_browser_video_upload_to_real_decoder")
                        page.locator("#source").select_option(str(face_source_id))
                        page.locator("#seconds").fill("2")
                        page.locator("#suggest").click()
                        expect(page.locator("#candidates [data-adopt]").first).to_be_visible(
                            timeout=20000)
                        page.locator("#candidate_0").fill("测试镜头（人工命名）")
                        page.locator("#candidates [data-adopt]").first.click()
                        expect(page.locator("#segments")).to_contain_text(
                            "测试镜头（人工命名）",timeout=12000)
                        assert page.locator("#graph").locator("svg").count()==1
                        expect(page.locator("#characters")).to_contain_text("没有任何人工绑定到真实视频帧的虚构角色")
                        checks.append("real_file_scene_cut_to_manual_storyboard_no_fake_character")
                        page.screenshot(path=str(OUT/"ui-anime-storyboard.png"),full_page=True)
                        # Canonical grouping acceptance: a shot is NOT a venue,
                        # and the entire movie is NOT bound to the camera source place.
                        page.locator("#venueName").fill("动画学校A")
                        page.locator("#createVenue").click()
                        expect(page.locator("#venueSelect")).to_contain_text(
                            "动画学校A",timeout=12000)
                        page.locator("#groupName").fill("教室")
                        page.locator("#createGroup").click()
                        expect(page.locator("#assignGroup")).to_contain_text(
                            "动画学校A / 教室",timeout=12000)
                        page.locator("#assign").click()
                        expect(page.locator("#segments")).to_contain_text(
                            "动画学校A / 教室",timeout=12000)
                        graph=page.evaluate("""async(sourceId)=>{
                            const r=await fetch('/api/anime/scenes/sources/'+sourceId+'/graph',
                                                {headers:{Authorization:'Bearer '+
                                                sessionStorage.getItem('zd_access_token')}});
                            return await r.json();
                        }""",face_source_id)
                        assert graph['canonical_venues'][0]['name']=="动画学校A",graph
                        assert graph['canonical_scenes'][0]['name']=="教室",graph
                        assert graph['characters']=={},graph
                        checks.append("manual_canonical_anime_venue_and_scene_group_browser")
                        page.screenshot(path=str(OUT/"ui-anime-canonical-groups.png"),full_page=True)
                        # This is a controlled local EVENT FIXTURE for the
                        # REVIEW UI, not proof that a detector found a person.
                        # Earlier real-CAVIAR full-media proof remains separate.
                        with main.db() as check:
                            check.execute("INSERT OR IGNORE INTO frigate_camera_bindings(camera,place_id) VALUES(?,?)",
                                          ("qa_camera_fixture",1))
                            check.execute("""INSERT INTO frigate_object_events
                              (camera,event_id,place_id,label,started_at,score,has_snapshot,updated_at)
                              VALUES ('qa_camera_fixture','test-track-1',1,'person',?,0.81,0,?)""",
                              (main.utcnow(),main.utcnow()))
                        page.goto(base+"/cctv")
                        expect(page.locator("#qaOccurrence")).to_contain_text(
                            "qa_camera_fixture",timeout=12000)
                        expect(page.locator("#qaStatus")).to_contain_text(
                            "not_reviewed",timeout=12000)
                        page.locator("#qaVerdict").select_option("needs_review")
                        page.locator("#qaReason").fill("浏览器人工质检测试样本：目标框仍需复查")
                        page.locator("#qaSave").click()
                        expect(page.locator("#qaStatus")).to_contain_text(
                            "needs_review",timeout=12000)
                        expect(page.locator("#qaHistory")).to_contain_text(
                            "浏览器人工质检测试样本",timeout=12000)
                        expect(page.locator("#occurrenceCounts")).to_contain_text(
                            "待审 1",timeout=12000)
                        expect(page.locator("#occurrenceCounts")).to_contain_text(
                            "1 条原始匿名事件",timeout=12000)
                        with main.db() as check:
                            assert check.execute(
                                "SELECT COUNT(*) FROM anonymous_occurrence_reviews").fetchone()[0]==1
                            check.execute("""UPDATE frigate_object_events SET score=0.92,
                               updated_at=? WHERE camera='qa_camera_fixture'
                               AND event_id='test-track-1'""",(main.utcnow(),))
                        page.locator("#qaOccurrence").dispatch_event("change")
                        expect(page.locator("#qaStatus")).to_contain_text(
                            "stale_review",timeout=12000)
                        page.locator("#loadOccurrences").click()
                        expect(page.locator("#occurrenceCounts")).to_contain_text(
                            "过期 1",timeout=12000)
                        expect(page.locator("#occurrenceCounts")).to_contain_text(
                            "1 条原始匿名事件",timeout=12000)
                        checks.append("real_browser_qa_raw_vs_reviewed_window_consistency")
                        checks.append("real_browser_append_only_anonymous_quality_and_stale_evidence")
                        page.screenshot(path=str(OUT/"ui-anonymous-quality-review.png"),full_page=True)
                        # This is a deliberately controlled RESEARCH STUDY
                        # fixture: two known local events, two cameras in the
                        # SAME venue, no inferred real-world person identity.
                        from datetime import datetime, timezone, timedelta
                        from app import site_model
                        with main.db() as check:
                            now=datetime.now(timezone.utc)+timedelta(seconds=2)
                            for name in ("browser_research_cam_a","browser_research_cam_b"):
                                check.execute("""INSERT OR IGNORE INTO frigate_camera_bindings
                                  (camera,place_id) VALUES(?,1)""",(name,))
                                site_model.record_frigate_initial(check,name,1)
                                epoch=check.execute("""SELECT id FROM frigate_site_binding_history
                                  WHERE camera=? AND valid_until IS NULL""",(name,)).fetchone()[0]
                                check.execute("""INSERT INTO frigate_object_events
                                  (camera,event_id,place_id,label,started_at,score,has_snapshot,
                                   updated_at,binding_epoch_id)
                                  VALUES (?,'same-local-demo',1,'person',?,0.84,0,?,?)""",
                                  (name,now.isoformat(),main.utcnow(),epoch))
                        page.reload()
                        expect(page.locator("#researchPanel")).to_be_visible(timeout=12000)
                        for name in ("browser_research_cam_a","browser_research_cam_b"):
                            option=page.locator("#qaOccurrence option").filter(has_text=name).first
                            key=option.get_attribute("value")
                            assert key
                            page.locator("#qaOccurrence").select_option(key)
                            page.locator("#qaVerdict").select_option("valid_local_target")
                            page.locator("#qaReason").fill("实际浏览器控制研究录像目标质检验收")
                            page.locator("#qaSave").click()
                            expect(page.locator("#qaStatus")).to_contain_text(
                                "valid_local_target",timeout=12000)
                            page.locator("#researchReason").fill(
                                "人工研究标签D001经控制样本核对，不代表真人自动识别")
                            page.locator("#researchAttach").click()
                            expect(page.locator("#researchStatus")).to_contain_text(
                                "当前有效",timeout=12000)
                        expect(page.locator("#researchRows")).to_contain_text(
                            "D001",timeout=12000)
                        expect(page.locator("#researchRows tr").first.locator("td").nth(3)
                               ).to_have_text("2",timeout=12000)
                        with main.db() as check:
                            assert check.execute("SELECT COUNT(*) FROM research_occurrence_links"
                                  ).fetchone()[0]==2
                        checks.append("browser_manual_pseudonymous_link_two_cameras_one_candidate_visit")
                        page.screenshot(path=str(OUT/"ui-manual-research-linkage.png"),full_page=True)
                        # Verify end users can REVIEW item 221, not only call
                        # its direct backend URL. This is a deliberate fake
                        # upstream event fixture, not a real detected person.
                        with main.db() as check:
                            check.execute("""INSERT OR IGNORE INTO frigate_camera_bindings
                                (camera,place_id) VALUES('zz_qa_page_demo',1)""")
                            check.executemany("""INSERT INTO frigate_object_events
                              (camera,event_id,place_id,label,started_at,score,has_snapshot,updated_at)
                              VALUES('zz_qa_page_demo',?,1,'person',?,0.84,0,?)""",
                              [(f'browser_{i:04}',main.utcnow(),main.utcnow())
                               for i in range(220)])
                        page.reload()
                        expect(page.locator("#qaNext")).to_be_enabled(timeout=12000)
                        page.locator("#qaNext").click()
                        expect(page.locator("#qaPageStatus")).to_contain_text("当前第 2 页",timeout=12000)
                        last_key=page.locator("#qaOccurrence option").last.get_attribute("value")
                        page.locator("#qaOccurrence").select_option(last_key)
                        expect(page.locator("#qaStatus")).to_contain_text("not_reviewed",timeout=12000)
                        page.locator("#qaReason").fill("实际浏览器第二页审核，验证超过200条事件仍可达")
                        page.locator("#qaSave").click()
                        expect(page.locator("#qaStatus")).to_contain_text(
                            "valid_local_target",timeout=12000)
                        with main.db() as check:
                            assert check.execute("""SELECT COUNT(*) FROM anonymous_occurrence_reviews
                                 WHERE occurrence_key=?""",(last_key,)).fetchone()[0]==1
                        checks.append("browser_page_221_local_occurrence_quality_reachable")
                        page.screenshot(path=str(OUT/"ui-anonymous-quality-page2.png"),full_page=True)
                        page.goto(base+"/portal")
                        page.set_viewport_size({"width": 390, "height": 844})
                        page.screenshot(path=str(OUT / "ui-mobile.png"), full_page=True)
                        checks.append("mobile_render")
                        page.locator("#logout").click()
                        expect(page).to_have_url(base + "/login")
                        checks.append("logout")
                        assert not errors, "JavaScript errors: " + str(errors)
                        checks.append("no_page_errors")
                    finally:
                        browser.close()
            return 0
        finally:
            if server and server.poll() is None:
                server.terminate()
                try: server.wait(timeout=5)
                except subprocess.TimeoutExpired: server.kill()
            (OUT / "ui-smoke-result.json").write_text(
                json.dumps({"passed_checks":checks,"count":len(checks)},
                           ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    raise SystemExit(run())
