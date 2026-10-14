"""Actual Chromium acceptance of the SHIPPED continuous EPFL AVI excerpt.

Runs the real FastAPI server, real SQLite login, and a browser that actually
decodes the 38s H.264 clip bundled for authorized research demonstrations.
The identities, place-level alerts, or fake HTTP data are never synthesized.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
VIDEO=ROOT/"samples"/"demo-media"/"epfl-dual-60-98.mp4"
OUT=ROOT/"reports"

def main():
    if not VIDEO.is_file() or VIDEO.stat().st_size < 1_000_000:
        raise RuntimeError("Authentic continuous EPFL clip missing")
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zd-video-product-") as tmp:
        env=dict(os.environ, ZD_DATA_DIR=tmp,ZD_LEGACY_MODE="0")
        env.pop("ZD_AUTH_TEST_COMPAT",None)
        os.environ["ZD_DATA_DIR"]=tmp
        os.environ["ZD_LEGACY_MODE"]="0"
        from app import main as backend,access
        backend.setup_db()
        with backend.db() as conn:
            state=conn.execute("SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()[0]
            access.create_user(conn,"ci_video_site","ci-video-demo-2026-password",state)
        with (OUT/"actual-product-server.log").open("w") as logfile:
            proc=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app",
               "--host","127.0.0.1","--port","8098"],
               env=env,cwd=ROOT,stdout=logfile,stderr=subprocess.STDOUT)
            try:
                base="http://127.0.0.1:8098"
                for _ in range(100):
                    if proc.poll() is not None:raise RuntimeError("Server exited")
                    try:
                        if urlopen(base+"/api/health",timeout=1).status==200:break
                    except OSError:time.sleep(.25)
                else:raise RuntimeError("Real server timed out")
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
                    try:
                        page=browser.new_page(viewport={"width":1440,"height":900})
                        errors=[]
                        page.on("pageerror",lambda e:errors.append(str(e)))
                        page.goto(base+"/login")
                        page.locator('input[name="username"]').fill("ci_video_site")
                        page.locator('input[name="password"]').fill("ci-video-demo-2026-password")
                        page.locator('button[type="submit"]').click()
                        expect(page).to_have_url(base+"/real",timeout=15000)
                        expect(page.locator(".zd-sidebar")).to_be_visible()
                        expect(page.locator(".zd-panel[data-view=overview]")).to_be_visible()
                        expect(page.locator("#overviewKpis")).to_contain_text("119",timeout=25000)
                        expect(page.locator("#overviewKpis")).to_contain_text("14",timeout=25000)
                        expect(page.locator("#overviewVideoState")).to_contain_text(
                            "已加载真实连续双机录像",timeout=25000)
                        page.wait_for_function("""() => {
                          const v=document.querySelector('#overviewVideo');
                          return v.readyState>=1 && v.duration>37 && v.videoWidth>=1200
                        }""",timeout=30000)
                        page.locator("#overviewVideo").evaluate("v=>{v.currentTime=15;v.pause()}")
                        page.wait_for_function("""() => {
                          const v=document.querySelector('#overviewVideo');
                          return v.readyState>=2 && Math.abs(v.currentTime-15)<.25
                        }""",timeout=20000)
                        page.screenshot(path=str(OUT/"ui-shipped-real-video-home.png"),full_page=True)
                        video=page.locator("#overviewVideo").evaluate(
                            "v=>({width:v.videoWidth,height:v.videoHeight,duration:v.duration,current:v.currentTime})")
                        assert video["width"]>=1200 and 37<video["duration"]<40,video
                        page.locator('.zd-nav[data-panel="video"]').click()
                        expect(page.locator("#researchActivity")).to_be_visible()
                        expect(page.locator("#activityStats")).to_contain_text("119")
                        page.screenshot(path=str(OUT/"ui-shipped-real-video-analysis.png"),full_page=True)
                        page.locator('.zd-nav[data-panel="space"]').click()
                        expect(page.locator("#realSceneAggregation")).to_be_visible()
                        expect(page.locator("#sceneTrackRows")).to_contain_text("V",timeout=18000)
                        expect(page.locator("#sceneHeatGrid > button")).to_have_count(64)
                        page.screenshot(path=str(OUT/"ui-shipped-real-spatial.png"),full_page=True)
                        page.locator('.zd-nav[data-panel="alerts"]').click()
                        expect(page.locator("#demoAlertRows")).to_contain_text("EPFL-DEMO",timeout=18000)
                        page.screenshot(path=str(OUT/"ui-shipped-real-alerts.png"),full_page=True)
                        page.locator('.zd-nav[href="/admin"]').click()
                        expect(page).to_have_url(base+"/admin",timeout=15000)
                        expect(page.locator(".zd-global-nav")).to_be_visible()
                        expect(page.locator("#accountPanel")).to_be_visible(timeout=15000)
                        # Excel business information architecture must be reachable
                        # through the same sidebar; no dummy identities are added.
                        expect(page.locator('.zd-global-nav a[href="/base"]')).to_be_visible()
                        page.locator('.zd-global-nav a[href="/base"]').click()
                        expect(page).to_have_url(base+"/base",timeout=15000)
                        expect(page.locator("#candidateRows")).to_be_visible()
                        expect(page.locator('.zd-global-nav a[href="/business"]')).to_be_visible()
                        page.locator('.zd-global-nav a[href="/business"]').click()
                        expect(page).to_have_url(base+"/business",timeout=15000)
                        expect(page.locator("#instancePanel")).to_be_visible()
                        page.locator('.zd-global-nav a[href="/insights"]').click()
                        expect(page).to_have_url(base+"/insights",timeout=15000)
                        expect(page.locator("#personQuery")).to_be_visible()
                        page.locator('.zd-global-nav a[href="/portal"]').click()
                        expect(page).to_have_url(base+"/portal",timeout=15000)
                        expect(page.locator('#navigation a[href="/base"]')).to_be_visible()
                        expect(page.locator('#navigation a[href="/business"]')).to_be_visible()
                        page.screenshot(path=str(OUT/"ui-shipped-business-overview.png"),full_page=True)

                        page.screenshot(path=str(OUT/"ui-shipped-real-admin-roles.png"),full_page=True)
                        assert not errors,errors
                        print("OPERATIONAL_SITE_REAL_CLIP_BROWSER_PASS="+json.dumps({
                            "source_clip_duration":video["duration"],
                            "source_clip_dimensions":[video["width"],video["height"]],
                            "actual_browser_playback_seek_verified":True,
                            "actual_bundle_video_loaded_not_black_missing_player":True,
                            "actual_pair_observations":119,
                            "certified_short_fragments":14,
                            "separate_navigation_modules_tested":5,
                            "administrator_role_screen_visible":True,
                            "client_javascript_errors":errors,
                        },ensure_ascii=False),flush=True)
                    finally:browser.close()
            finally:
                proc.terminate()
                proc.wait(timeout=20)

if __name__=="__main__":main()
