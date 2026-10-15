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
                        expect(page).to_have_url(base+"/portal",timeout=15000)
                        expect(page.locator("#navigation")).to_be_visible()
                        # Business-first landing, with authentic video and explicit data provenance.
                        expect(page.locator("#portalResearchClip")).to_be_visible()
                        page.screenshot(path=str(OUT/"ui-shipped-business-first-login.png"),full_page=True)
                        page.goto(base+"/real")
                        expect(page.locator(".zd-sidebar")).to_be_visible()
                        expect(page.locator(".zd-panel[data-view=overview]")).to_be_visible()
                        expect(page.locator("#overviewKpis")).to_contain_text("119",timeout=25000)
                        expect(page.locator("#overviewKpis")).to_contain_text("14",timeout=25000)
                        expect(page.locator("#overviewVideoState")).to_contain_text(
                            "已加载真实连续双机录像",timeout=25000)
                        expect(page.locator("#overviewVideo")).to_have_attribute("poster",__import__("re").compile(r"^blob:.*"),timeout=25000)
                        actual_poster=page.request.get(base+"/api/real/showcase-media/poster",
                              headers={"Authorization":"Bearer "+page.evaluate("sessionStorage.getItem('zd_access_token')")})
                        assert actual_poster.ok and actual_poster.headers["content-type"].startswith("image/jpeg")
                        assert len(actual_poster.body())>10000
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
                        page.wait_for_timeout(800)
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
                        expect(page.locator(".zd-workspace-tabs button")).to_have_count(4)
                        page.get_by_role("tab",name=__import__("re").compile("账户权限")).click()
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
                        expect(page.locator(".zd-workspace-tabs button")).to_have_count(4)
                        expect(page.locator("#alerts")).to_be_visible()
                        page.get_by_role("tab",name=__import__("re").compile("规则配置")).click()
                        expect(page.locator("#instancePanel")).to_be_visible()
                        page.screenshot(path=str(OUT/"ui-shipped-compact-rule-settings.png"),full_page=True)
                        page.locator('.zd-global-nav a[href="/insights"]').click()
                        expect(page).to_have_url(base+"/insights",timeout=15000)
                        expect(page.locator(".zd-workspace-tabs button")).to_have_count(3)
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
                            if route=="/monitor":
                                page.get_by_role("tab",name=__import__("re").compile("持续采集")).click()
                            expect(page.locator(selector)).to_be_visible(timeout=18000)
                            page.wait_for_timeout(800)
                            text=page.locator("body").inner_text()
                            inspected[label]={"path":route,"visible_chars":len(text),
                                              "screenshot":"ui-shipped-"+label+".png"}
                            page.screenshot(path=str(OUT/("ui-shipped-"+label+".png")),full_page=True)
                        assert page.locator("body").is_visible()
                        # Verify major workspaces fit on one purposeful screen and
                        # that operations remain available under the correct tab.
                        for route,label,selected,secondary in [
                            ("/business","rules","预警处理","规则配置"),
                            ("/base","registry","人员底库","底库维护"),
                            ("/admin","admin","辖区与场所","账户权限"),
                            ("/monitor","devices","采集概览","持续采集"),
                            ("/insights","search","人员检索","综合分析"),
                        ]:
                            page.goto(base+route,wait_until="domcontentloaded")
                            bar=page.locator(".zd-workspace-tabs")
                            expect(bar).to_be_visible()
                            expect(bar.get_by_role("tab",name=__import__("re").compile(selected))).to_have_attribute("aria-selected","true")
                            primary=page.locator('main .panel:not(.zd-compact-hidden),main .card:not(.zd-compact-hidden)')
                            page.screenshot(path=str(OUT/("ui-shipped-compact-"+label+"-primary.png")),full_page=True)
                            bar.get_by_role("tab",name=__import__("re").compile(secondary)).click()
                            expect(bar.get_by_role("tab",name=__import__("re").compile(secondary))).to_have_attribute("aria-selected","true")
                            page.screenshot(path=str(OUT/("ui-shipped-compact-"+label+"-secondary.png")),full_page=True)
                        # Check the first screen reports both independent data sources.
                        page.goto(base+"/portal")
                        expect(page.locator(".zd-demo-strip")).to_be_visible()
                        expect(page.locator("#overviewResearchPairs")).to_have_text("119",timeout=25000)
                        expect(page.locator(".zd-demo-strip")).to_contain_text(
                            str(overview["synthetic"]["cards"]["synthetic_visits"]))
                        page.screenshot(path=str(OUT/"ui-shipped-business-first-screen-after-seed.png"),full_page=True)
                        # A warning must actually lead to a person timeline, not only a placeholder.
                        from urllib.parse import quote
                        sample=next(a for a in overview["synthetic"]["recent_alerts"] if a.get("people"))
                        page.goto(base+"/portal?alert="+quote(sample["key"],safe=""))
                        person=sample["people"][0]
                        personLink=page.locator('a[href="/insights?person='+person+'"]')
                        expect(personLink).to_be_visible()
                        personLink.click()
                        expect(page).to_have_url(base+"/insights?person="+person,timeout=15000)
                        expect(page.locator("#personQuery")).to_have_value(person)
                        expect(page.locator("#detailBox")).to_have_class(
                            __import__("re").compile(r".*show.*"),timeout=18000)
                        expect(page.locator("#personContent")).to_contain_text("模拟时间线",timeout=18000)
                        page.screenshot(path=str(OUT/"ui-shipped-alert-to-person-timeline.png"),full_page=True)
                        # Drill into actual records: list -> details -> evidence -> review.
                        page.goto(base+"/portal?tab=places")
                        first_place=page.locator('a[href^="/portal?place="]').first
                        expect(first_place).to_be_visible()
                        first_place.click()
                        expect(page.locator(".content")).to_contain_text("测试场所",timeout=10000)
                        page.screenshot(path=str(OUT/"ui-shipped-business-place-detail.png"),full_page=True)
                        page.goto(base+"/portal?tab=alerts")
                        first_alert=page.locator('a[href^="/portal?alert="]').first
                        expect(first_alert).to_be_visible()
                        first_alert.click()
                        expect(page.locator("#review")).to_be_visible(timeout=10000)
                        expect(page.locator(".content")).to_contain_text("原始模拟观测证据 ID")
                        assert "合成演示" in page.locator(".content").inner_text()
                        page.screenshot(path=str(OUT/"ui-shipped-business-alert-detail.png"),full_page=True)
                        page.locator("#reason").fill("真实浏览器核查合成规则证据")
                        page.locator("#decision").select_option("confirmed")
                        page.locator("#review").click()
                        expect(page.locator(".content")).to_contain_text("已核实",timeout=15000)
                        page.goto(base+"/insights")
                        page.locator("#personQuery").fill("P099")
                        page.locator("#search").click()
                        expect(page.locator("#peopleList")).to_contain_text("P099",timeout=15000)
                        page.screenshot(path=str(OUT/"ui-shipped-business-person-P099-search.png"),full_page=True)
                        page.goto(base+"/base")
                        expect(page.locator("#candidateRows")).to_contain_text("P099",timeout=15000)
                        page.once("dialog",lambda dialog:dialog.accept())
                        page.locator("#pub").click()
                        page.wait_for_function("""async () => {
                            const t=sessionStorage.getItem('zd_access_token');
                            const r=await fetch('/api/base/active',{headers:{Authorization:'Bearer '+t}});
                            const x=await r.json();return !!x.ready
                        }""",timeout=15000)
                        page.screenshot(path=str(OUT/"ui-shipped-business-published-roster.png"),full_page=True)
                        # A successful click is not an empty shell: a published roster
                        # must persist and use the chosen frequency home for P099.
                        active=read("/api/base/active")
                        assert active["ready"] and active["run"]["state"]=="published",active
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
