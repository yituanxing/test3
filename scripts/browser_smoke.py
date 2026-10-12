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
