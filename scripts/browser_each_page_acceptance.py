"""Independent, per-page real-Chromium acceptance of the shipped web product.
Invoked inside existing real-video browser test; no mock network responses,
no extra video downloads, no separate server, and no test-only fake identities.
"""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import expect

ROUTES=[
 ('/portal','综合态势','#navigation'),
 ('/portal?tab=places','场所列表','.content'),
 ('/portal?tab=alerts','预警中心','.content'),
 ('/portal?tab=acceptance','Excel23项','#acceptanceRows'),
 ('/real','真实双机视频','#overviewVideo'),
 ('/recognition-results','视觉识别成果','#researchPoster'),
 ('/insights','人员活动与检索','#realActivityRows'),
 ('/cctv','原始图像与人工核对','#epflResearchPanel'),
 ('/business','业务规则与审核','#alerts'),
 ('/base','场所人员底库','#candidateRows'),
 ('/monitor','视频源与采集','#capturePanel'),
 ('/admin','辖区与账号','#accountPanel'),
 ('/geo','示意场所地图','#map'),
 ('/anime-scenes','动画场景实验','main'),
 ('/face-lab','授权照片实验','#modelPanel')
]
WORKSPACES={
 '/insights':['recorded','situation','search','real'],
 '/cctv':['epfl','caviar','review','limits'],
 '/business':['alerts','rules','instances','people','evidence','history'],
 '/base':['people','maintenance','versions','audit'],
 '/monitor':['overview','events','capture','proof'],
 '/admin':['places','sources','devices','statistics','accounts','audit']
}

def verify_all_business_pages(page,base,out,read):
    """Fail acceptance for any broken page/task; persist evidence even on failure."""
    out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    report={'baseline':'real research video + separately initialized synthetic business',
            'checks':['real authenticated browser','visible content','all task tabs',
                      'data provenance','desktop overflow','Excel missing items preserved'],
            'total_pages':len(ROUTES),'pages':[],'failures':[]}
    for n,(route,title,selector) in enumerate(ROUTES,1):
        result={'route':route,'page':title,'status':'NOT_RUN','tasks':[],'images':[]}
        report['pages'].append(result)
        try:
            response=page.goto(base+route,wait_until='domcontentloaded',timeout=25000)
            assert response and response.status<400,(route,response.status if response else None)
            assert page.url.startswith(base+route.split('?')[0]),page.url
            if route=='/monitor':
                page.locator('.zd-workspace-tabs [data-tab="capture"]').click()
            elif route=='/admin':
                page.locator('.zd-workspace-tabs [data-tab="accounts"]').click()
            expect(page.locator(selector).first).to_be_visible(timeout=20000)
            page.wait_for_timeout(300)
            if route=='/real':
                # This is what a presenter sees after loading and seeking the real
                # AVI-derived clip; a black unloaded player is not proof of video.
                page.wait_for_function(
                    "() => document.querySelector('#overviewVideo')?.videoWidth>=1200",
                    timeout=20000)
                page.locator('#overviewVideo').evaluate("(v)=>{v.currentTime=15;v.pause()}")
                page.wait_for_function(
                    "() => {const v=document.querySelector('#overviewVideo'); return v.readyState>=2&&Math.abs(v.currentTime-15)<.3}",
                    timeout=20000)
            layout=page.evaluate("""() => ({
               viewport:innerWidth,width:document.documentElement.scrollWidth,
               height:document.documentElement.scrollHeight,
               text:(document.body.innerText||'').trim().length,
               heading:document.querySelector('main h1')?.innerText||'',
               pageTitle:document.title})""")
            assert layout['text']>100,(route,'blank screen',layout)
            assert layout['width']<=layout['viewport']+3,(route,'horizontal overflow',layout)
            result['layout']=layout
            # The old pages were 2–3 screens high while navigation was only one
            # screen. Reject that geometry, then prove an actual mouse wheel moves
            # the RIGHT pane without moving the fixed sidebar or document.
            metrics=page.evaluate("""() => {
              const p=location.pathname;
              const pane=p==='/portal'?document.querySelector('#view'):
                p==='/real'?document.querySelector('.zd-work main'):
                document.querySelector('.zd-global-body > main');
              const nav=p==='/portal'?document.querySelector('.layout>aside'):
                p==='/real'?document.querySelector('.zd-sidebar'):
                document.querySelector('.zd-global-nav');
              return {pane:!!pane, nav:!!nav,
                 scrollHeight:pane?.scrollHeight||0,clientHeight:pane?.clientHeight||0,
                 windowHeight:innerHeight,documentHeight:document.documentElement.scrollHeight,
                 navHeight:nav?.getBoundingClientRect().height||0,
                 top:pane?.scrollTop||0,navTop:nav?.scrollTop||0}
            }""")
            assert metrics['pane'] and metrics['nav'],(route,metrics)
            assert metrics['documentHeight']<=metrics['windowHeight']+5,(route,'long document',metrics)
            assert abs(metrics['navHeight']-metrics['windowHeight'])<=5,(route,'sidebar size mismatch',metrics)
            if metrics['scrollHeight']>metrics['clientHeight']+65:
                page.mouse.move(1350,500)
                page.mouse.wheel(0,560)
                page.wait_for_timeout(260)
                after=page.evaluate("""() => {
                   const p=location.pathname;
                   const el=p==='/portal'?document.querySelector('#view'):
                     p==='/real'?document.querySelector('.zd-work main'):
                     document.querySelector('.zd-global-body>main');
                   return el?.scrollTop||0}""")
                assert after>metrics['top']+35,(route,'wheel failed to scroll right content',metrics,after)
                result['wheel_scroll_pixels']=after-metrics['top']
                page.mouse.wheel(0,-2000)
                page.wait_for_timeout(150)
            slug=f'{n:02d}-'+route.strip('/').replace('/','-').replace('?','-').replace('=','-')
            screenshot=f'accept-page-{slug}.png'
            page.screenshot(path=str(out/screenshot),full_page=True)
            result['images'].append(screenshot)
            if route in WORKSPACES:
                bar=page.locator('.zd-workspace-tabs')
                expect(bar).to_be_visible()
                tabs=bar.locator('[role="tab"]')
                ids=tabs.evaluate_all('n=>n.map(x=>x.dataset.tab)')
                assert ids==WORKSPACES[route],(route,ids)
                for task in ids:
                    button=bar.locator('[role="tab"][data-tab="'+task+'"]')
                    button.click()
                    expect(button).to_have_attribute('aria-selected','true')
                    selected=tabs.evaluate_all('n=>n.filter(x=>x.getAttribute("aria-selected")==="true").map(x=>x.dataset.tab)')
                    assert selected==[task],(route,task,selected)
                    sections=page.evaluate("""() => [...document.querySelectorAll('main section,main .panel')]
                      .filter(x=>getComputedStyle(x).display!=='none'&&x.getClientRects().length>0).length""")
                    assert sections>0,(route,task,'empty task')
                    result['tasks'].append({'name':task,'status':'PASS'})
                    if task in ('recorded','caviar','epfl','search','rules','capture','accounts','audit'):
                        image=f'accept-task-{slug}-{task}.png'
                        page.screenshot(path=str(out/image),full_page=True)
                        result['images'].append(image)
                if route=='/insights':
                    bar.locator('[data-tab="recorded"]').click()
                    expect(page.locator('#realPairs')).to_have_text('119',timeout=20000)
                    expect(page.locator('#realActivityRows')).to_contain_text('V',timeout=20000)
                    assert page.locator('#peopleList').is_hidden()
                if route=='/cctv':
                    bar.locator('[data-tab="epfl"]').click()
                    expect(page.locator('#epflResearchPanel')).to_be_visible()
                if route=='/admin':
                    bar.locator('[data-tab="accounts"]').click()
                    expect(page.locator('#accountPanel')).to_be_visible()
            if route=='/real':
                for tab in ['overview','video','space','alerts','acceptance']:
                    button=page.locator('.zd-nav[data-panel="'+tab+'"]')
                    expect(button).to_be_visible()
                    button.click()
                    expect(page.locator('.zd-panel[data-view="'+tab+'"]')).to_be_visible()
                    result['tasks'].append({'name':tab,'status':'PASS'})
                page.locator('.zd-nav[data-panel="overview"]').click()
                expect(page.locator('#overviewKpis')).to_contain_text('119',timeout=20000)
                page.wait_for_function("() => document.querySelector('#overviewVideo')?.videoWidth>=1200",timeout=20000)
            if route=='/recognition-results':
                expect(page.locator('#metricPairs')).to_have_text('119',timeout=20000)
                assert page.locator('#researchPoster').evaluate('(x)=>x.complete&&x.naturalWidth>200')
            if route=='/portal?tab=acceptance':
                expect(page.locator('#acceptanceRows tr')).to_have_count(23,timeout=15000)
                table=page.locator('.accept-table-scroller')
                expect(table).to_be_visible()
                assert table.evaluate('(e)=>e.scrollHeight>e.clientHeight+400')
                table.scroll_into_view_if_needed()
                box=table.bounding_box()
                page.mouse.move(box['x']+box['width']/2,box['y']+min(box['height']/2,320))
                page.mouse.wheel(0,720)
                page.wait_for_timeout(150)
                assert table.evaluate('(e)=>e.scrollTop')>250,'23-row table did not wheel-scroll'
                result['excel_table_scrolled']=True
                matrix=read('/api/demo/excel-acceptance')
                assert len(matrix['requirements'])==23 and sum(matrix['counts'].values())==23
                assert matrix['counts']['missing']==2 and matrix['counts']['synthetic_only']==10
                result['excel_counts']=matrix['counts']
            if route=='/face-lab':
                expect(page.locator('#modelBadge')).to_contain_text('暂不可运行',timeout=15000)
                assert page.locator('#enroll').is_disabled()
            if route=='/portal?tab=alerts':
                expect(page.locator('.content')).to_contain_text('合成',timeout=10000)
            if route=='/geo':
                assert ('合成' in page.locator('body').inner_text() or
                        '示意' in page.locator('body').inner_text())
                if read('/api/geo/places')['positioned']==0:
                    expect(page.locator('#geoMapEmpty')).to_be_visible(timeout=15000)
            result['status']='PASS'
        except Exception as exc:
            result['status']='FAIL'
            result['error']=str(exc)[:1600]
            report['failures'].append({'route':route,'error':str(exc)[:650]})
            try:
                image=f'accept-failure-{n:02d}.png'
                page.screenshot(path=str(out/image),full_page=True,timeout=7000)
                result['images'].append(image)
            except Exception:pass
    report['passed']=sum(x['status']=='PASS' for x in report['pages'])
    report['tasks_checked']=sum(len(x['tasks']) for x in report['pages'])
    (out/'every-page-acceptance.json').write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('EVERY_PAGE_ACCEPTANCE='+json.dumps({
        'passed':report['passed'],'total':report['total_pages'],
        'task_tabs':report['tasks_checked'],'failures':report['failures']},
        ensure_ascii=False),flush=True)
    assert not report['failures'],'Broken page(s): '+json.dumps(report['failures'],ensure_ascii=False)
    return report
