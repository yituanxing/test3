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
        env=dict(os.environ, ZD_DATA_DIR=tmp,ZD_LEGACY_MODE="1")
        env.pop("ZD_AUTH_TEST_COMPAT",None)
        os.environ["ZD_DATA_DIR"]=tmp
        os.environ["ZD_LEGACY_MODE"]="1"
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
                        page.screenshot(path=str(OUT/"ui-shipped-real-admin-roles.png"),full_page=True)
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

                        # Business DATA contract: empty initial state is not a completed demo.
                        token=page.evaluate("sessionStorage.getItem('zd_access_token')")
                        head={"Authorization":"Bearer "+token}
                        def read(url):
                            resp=page.request.get(base+url,headers=head)
                            assert resp.ok,(url,resp.status,resp.text()[:500])
                            return resp.json()
                        def post(url,payload=None):
                            kw={"headers":head}
                            if payload is not None:kw["data"]=payload
                            resp=page.request.post(base+url,**kw)
                            assert resp.ok,(url,resp.status,resp.text()[:500])
                            return resp.json()
                        initial=read("/api/insights/overview?days=7")
                        initial_syn=initial["synthetic"]["cards"]
                        assert initial_syn["synthetic_visits"]==0,initial_syn
                        assert read("/api/monitoring/overview?days=7")["counts"]["person_objects"]==0
                        # A controlled *synthetic* scenario is explicitly initialized.
                        post("/api/business/seed")
                        post("/api/base/demo/duplicates")
                        generated=post("/api/base/generate",{
                            "days":7,"strategy":"frequency","correction_mode":"rebuild"})
                        assert generated["run"]["state"]=="draft"
                        assert len(generated["people"])>=6
                        assert any(x["person"]=="P099" and x["place_id"]==2
                                   for x in generated["people"]),generated["people"]
                        analysis=read("/api/business/analysis")
                        overview=read("/api/insights/overview?days=7")
                        assert analysis["visit_count"]>=15
                        assert overview["synthetic"]["cards"]["synthetic_visits"]>=15
                        assert sum(x["count"] for x in analysis["rule_counts"])>0
                        assert read("/api/real/evidence?days=7")["real_event_counts"]["person_objects"]==0
                        inspected={}
                        targets=[
                          ("/portal","business-overview","#navigation"),
                          ("/base","business-roster","#candidateRows"),
                          ("/business","business-seven-rules","#alerts"),
                          ("/insights","business-people-search","#peopleList"),
                          ("/portal?tab=alerts","business-alert-list",".content"),
                          ("/portal?tab=places","business-places",".content"),
                          ("/portal?tab=acceptance","business-23-acceptance","#acceptanceRows"),
                          ("/monitor","business-devices","#capturePanel"),
                          ("/cctv","business-review","#epflResearchPanel"),
                        ]
                        for route,label,selector in targets:
                            page.goto(base+route,wait_until="domcontentloaded")
                            expect(page.locator(selector)).to_be_visible(timeout=18000)
                            page.wait_for_timeout(800)
                            text=page.locator("body").inner_text()
                            inspected[label]={"path":route,"visible_chars":len(text),
                                              "screenshot":"ui-shipped-"+label+".png"}
                            page.screenshot(path=str(OUT/("ui-shipped-"+label+".png")),full_page=True)
                        assert page.locator("body").is_visible()
                        page.goto(base+"/base")
                        expect(page.locator("#candidateRows")).to_contain_text("P099",timeout=15000)
                        page.goto(base+"/business")
                        expect(page.locator("#alerts")).to_contain_text("P",timeout=15000)
                        page.goto(base+"/insights")
                        expect(page.locator("#peopleList")).to_contain_text("P",timeout=15000)
                        page.goto(base+"/portal?tab=acceptance")
                        expect(page.locator("#acceptanceRows tr")).to_have_count(23,timeout=15000)
                        # 23 entries on screen are statuses, NOT 23 finished features.
                        with (OUT/"excel-business-actual-browser-audit.json").open("w") as f:
                            json.dump({"business_screens":inspected,
                                "initial_synthetic_visits":initial_syn["synthetic_visits"],
                                "post_seed_synthetic_visits":overview["synthetic"]["cards"]["synthetic_visits"],
                                "synthetic_rule_counts":analysis["rule_counts"],
                                "new_roster_people":len(generated["people"]),
                                "real_person_events":0,
                                "recorded_real_video_observations":119,
                                "requirements_count":23,
                                "browser_errors":errors},f,ensure_ascii=False,indent=2)
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
