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
