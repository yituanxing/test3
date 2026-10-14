"""Chromium smoke for the real-only UI; no fake camera event is generated."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports"


def main():
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        env = dict(os.environ, ZD_DATA_DIR=directory, ZD_LEGACY_MODE="0")
        env.pop("PYTEST_CURRENT_TEST", None)
        env.pop("ZD_AUTH_TEST_COMPAT", None)
        os.environ["ZD_DATA_DIR"] = directory
        os.environ["ZD_LEGACY_MODE"] = "0"
        from app import main as backend, access
        backend.setup_db()
        with backend.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM places").fetchone()[0] == 0
            root = conn.execute("SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()[0]
            access.create_user(conn, "ci_real_ui", "temporary-ci-ui-password-2026", root)
            county = conn.execute("SELECT id FROM regions WHERE level=2 ORDER BY id LIMIT 1").fetchone()[0]
            access.create_user(conn, "ci_county_ui", "temporary-county-ui-password-2026", county)
        with (OUT / "ui-real-server.log").open("w") as logfile:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host",
                 "127.0.0.1", "--port", "8094"], cwd=ROOT, env=env,
                stdout=logfile, stderr=subprocess.STDOUT)
            try:
                base = "http://127.0.0.1:8094"
                for _ in range(90):
                    if proc.poll() is not None:
                        raise RuntimeError("server exited")
                    try:
                        with urlopen(base + "/api/health", timeout=1):
                            break
                    except OSError:
                        time.sleep(0.25)
                else:
                    raise RuntimeError("server startup timed out")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                    try:
                        page = browser.new_page(viewport={"width": 1280, "height": 900})
                        errors = []
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        page.goto(base + "/portal")
                        expect(page).to_have_url(base + "/login")
                        page.screenshot(path=str(OUT / "ui-page-login.png"), full_page=True)
                        page.locator('input[name="username"]').fill("ci_real_ui")
                        page.locator('input[name="password"]').fill("temporary-ci-ui-password-2026")
                        page.locator('button[type="submit"]').click()
                        expect(page).to_have_url(base + "/real", timeout=12000)
                        expect(page.locator("#realEvents")).to_have_text("0", timeout=12000)
                        expect(page.locator("#places")).to_contain_text("本辖区尚无场所")
                        assert page.locator('a[href="/business"]').count() == 0
                        expect(page.locator("#videoClipPlaceholder")).to_be_visible()
                        page.locator("#videoClipPicker").set_input_files(
                            str(ROOT / "samples" / "test-pattern.mp4"))
                        expect(page.locator("#videoClipStatus")).to_contain_text(
                            "请选择CI研究视频工件")
                        expect(page.locator("#videoClip")).to_be_hidden()
                        actual_clip = os.getenv("ZD_REAL_EPFL_CLIP_FILE")
                        if actual_clip:
                            page.locator("#videoClipPicker").set_input_files(
                                str(Path(actual_clip).resolve()))
                            expect(page.locator("#videoClip")).to_be_visible(timeout=15000)
                            expect(page.locator("#videoClipStatus")).to_contain_text(
                                "已打开本地实际视频")
                            page.evaluate("() => { clipFPS=25; seekVideoSourceFrame(1875); }")
                            page.wait_for_function(
                                "() => { const v=document.querySelector('#videoClip');"
                                "return v.readyState>=2 && Math.abs(v.currentTime-15)<.2; }",
                                timeout=15000)
                            page.locator("#videoClip").screenshot(
                                path=str(OUT / "ui-actual-epfl-clip-frame.png"))
                            print("REAL_VIDEO_BROWSER_SEEK_AND_DECODE_PASS")
                        page.screenshot(path=str(OUT / "ui-real-only-landing.png"), full_page=True)
                        page.goto(base + "/monitor")
                        assert page.locator('a[href="/business"]').count() == 0
                        page.wait_for_timeout(500)
                        page.screenshot(path=str(OUT / "ui-page-monitor-real.png"), full_page=True)
                        page.goto(base + "/cctv")
                        assert page.locator('a[href="/base"]').count() == 0
                        page.wait_for_timeout(500)
                        page.screenshot(path=str(OUT / "ui-page-cctv-research.png"), full_page=True)
                        page.goto(base + "/admin")
                        page.wait_for_timeout(500)
                        page.screenshot(path=str(OUT / "ui-page-admin-regions-and-roles.png"), full_page=True)
                        page.goto(base + "/real")
                        page.locator("#logout").click()
                        expect(page).to_have_url(base + "/login", timeout=10000)
                        page.locator('input[name="username"]').fill("ci_county_ui")
                        page.locator('input[name="password"]').fill("temporary-county-ui-password-2026")
                        page.locator('button[type="submit"]').click()
                        expect(page).to_have_url(base + "/real", timeout=12000)
                        expect(page.locator("#researchActivity")).to_be_hidden(timeout=12000)
                        expect(page.locator("#excelAcceptance")).to_be_hidden(timeout=12000)
                        page.screenshot(path=str(OUT / "ui-page-county-limited-access.png"), full_page=True)
                        assert not errors, str(errors)
                    finally:
                        browser.close()
            finally:
                proc.terminate()
                proc.wait(timeout=15)
    print("REAL_ONLY_BROWSER_PASS; no synthetic seed or camera event")


if __name__ == "__main__":
    main()
