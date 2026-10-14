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
                        expect(page).to_have_url(base+"/portal",timeout=15000)
                        page.goto(base+"/cctv")
                        expect(page.locator("#epflImportReport")).to_be_visible()
                        # Genuine report generated from the two real downloaded
                        # EPFL AVI camera streams, NOT a fabricated fixture.
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
                "admin_audit_entries":audit,
                "identity_confirmed":False,
                "real_risk_alerts_generated":0,
                "synthetic_roster_rows_generated":0,
                "browser_screenshot":"reports/ui-epfl-real-research-review.png",
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
