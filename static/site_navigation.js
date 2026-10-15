/* Unified ZD business navigation. Presentation never grants API permissions. */
(function(){
"use strict";
if(location.pathname==="/real"||location.pathname==="/portal"||location.pathname==="/login")return;
const pathname=location.pathname;
const pageNames={
 "/base":"场所人员底库 · 建立、归并、矫正（合成演示）",
 "/business":"预警规则与复核（合成演示）",
 "/insights":"智能检索与活动分析",
 "/cctv":"抓拍证据与人工复核",
 "/monitor":"视频设备与持续采集",
 "/admin":"辖区、场所与权限管理",
 "/geo":"活动区域与地图（合成演示）",
 "/face-lab":"授权照片实验",
 "/recognition-results":"视觉识别技术成果",
 "/anime-scenes":"动画视频场景实验",
};
if(!pageNames[pathname])return;
const styles=document.createElement("style");
styles.textContent=`
html body{margin:0!important;background:#f5f7fb}
.zd-global-shell{display:grid;grid-template-columns:238px minmax(0,1fr);min-height:100vh}
.zd-global-nav{position:sticky;top:0;box-sizing:border-box;height:100vh;overflow-y:auto;background:#142c47;color:#f0f6fe;padding:20px 13px;display:flex;flex-direction:column;gap:3px;font:14px/1.45 "Segoe UI","Microsoft YaHei",sans-serif;z-index:20}
.zd-global-nav .zd-brand-name{padding:8px 10px 17px;font-size:17px;font-weight:800;color:#fff}
.zd-global-nav .zd-brand-name small{display:block;font-size:11px;color:#aabdd0;font-weight:400}
.zd-global-nav .zd-nav-group{padding:15px 11px 5px;font-size:11px;color:#8faec9;letter-spacing:.08em}
.zd-global-nav a{display:block!important;color:#d9e7f5!important;border-radius:9px!important;padding:10px 12px!important;text-decoration:none!important;font-size:13px!important;font-weight:500!important;background:transparent!important;margin:1px 0!important}
.zd-global-nav a:hover,.zd-global-nav a.active{color:#fff!important;background:#27567b!important}
.zd-global-nav a.active{box-shadow:inset 3px 0 #76bdff;font-weight:750!important}
.zd-global-nav a small{display:inline;color:#a7c3d8;font-size:10px}
.zd-global-nav [hidden]{display:none!important}
.zd-global-nav .zd-global-note{padding:16px 10px 0;margin-top:auto;border-top:1px solid #31506b;font-size:11px;color:#adc4d8}
.zd-global-body{min-width:0}
.zd-global-body>.zd-global-top{display:flex;gap:18px;align-items:center;justify-content:space-between;min-height:62px;padding:12px 32px;background:#fff;border-bottom:1px solid #e3e9f0;color:#233d5b;font:14px/1.4 "Segoe UI","Microsoft YaHei",sans-serif}
.zd-global-top strong{font-size:16px}
.zd-global-top small{color:#74889a}
.zd-global-body>header,.zd-global-body>main>header{display:none!important}
.zd-global-body>main{width:auto!important;max-width:1370px!important;box-sizing:border-box;margin:0 auto!important;padding:22px 26px 65px!important}
.zd-global-body>main.wrap{max-width:1370px!important}
.zd-global-body>.wrap{max-width:1370px;margin:auto}
@media(min-width:751px){
 html,body{height:100%;overflow:hidden}
 .zd-global-shell{height:100dvh;min-height:0;overflow:hidden}
 .zd-global-nav{position:relative;top:auto;height:100%;min-height:0;overflow-y:auto;
  overscroll-behavior:contain;scrollbar-gutter:stable}
 .zd-global-body{height:100%;min-height:0;display:flex;flex-direction:column;overflow:hidden}
 .zd-global-body>.zd-global-top{flex:0 0 62px;min-height:62px;height:62px}
 .zd-global-body>main{flex:1;min-height:0;width:100%!important;max-width:none!important;
  overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;scrollbar-gutter:stable;
  padding:17px 23px 26px!important}
 .zd-global-body main .zd-workspace-tabs{position:sticky;top:0}
}
@media(max-width:750px){.zd-global-shell{display:block}.zd-global-nav{height:auto;position:static;display:flex;flex-direction:row;flex-wrap:wrap;gap:2px;padding:9px}.zd-global-nav .zd-brand-name{width:100%;padding:4px 8px 8px}.zd-global-nav .zd-nav-group,.zd-global-nav .zd-global-note{display:none}.zd-global-nav a{padding:7px 8px!important;font-size:12px!important}.zd-global-top small{display:none}.zd-global-body>.zd-global-top{padding:11px 17px;min-height:45px}.zd-global-body>main{padding:16px!important}}
`;
document.head.append(styles);
const root=document.createElement("div");root.className="zd-global-shell";
const aside=document.createElement("aside");aside.className="zd-global-nav";aside.setAttribute("aria-label","业务主导航");
aside.innerHTML=`<div class="zd-brand-name">ZD · 场所预警分析<small>多场所人员活动 · 业务演示系统</small></div>
 <div class="zd-nav-group">业务工作台</div>
 <a href="/portal" data-route="overview">◈　综合态势</a>
 <a href="/real#overview" data-route="video">▣　视频监控</a>
 <a href="/base" data-research-only="1" data-route="base">▤　场所人员底库 <small>演示</small></a>
 <a href="/portal?tab=alerts" data-route="alerts">♢　预警中心</a>
 <a href="/insights" data-route="insights">⌕　智能检索与分析</a>
 <div class="zd-nav-group">业务操作</div>
 <a href="/portal?tab=places" data-route="places">▦　场所列表</a>
 <a href="/business" data-research-only="1" data-route="rules">⚙　预警规则与复核 <small>演示</small></a>
 <a href="/cctv" data-route="cctv">▥　抓拍证据与人工复核</a>
 <a href="/recognition-results" data-route="recognition">◆　视觉识别成果</a>
 <a href="/anime-scenes" data-route="anime">◇　动画场景实验 <small>研究</small></a>
 <div class="zd-nav-group">基础管理</div>
 <a href="/monitor" data-route="monitor">◷　设备与采集</a>
 <a href="/admin" data-route="admin">⚑　辖区、场所与账号</a>
 <a href="/portal?tab=acceptance" data-research-only="1" data-route="acceptance">☑　需求验收 <small>管理</small></a>
<div class="zd-global-note">真实匿名视频、研究配对与合成人员分别管理；页面数据不等于真人身份结论。</div>`;
// Hide demo-only destinations until the server confirms that mode is enabled.
const deferredLinks=[...aside.querySelectorAll('[href="/portal"],[href="/portal?tab=places"],[href="/portal?tab=alerts"],[href="/insights"],[data-research-only]')].map(el=>({el,after:el.nextSibling,parent:el.parentNode}));
deferredLinks.forEach(x=>x.el.remove());
const content=document.createElement("div");content.className="zd-global-body";
const top=document.createElement("div");top.className="zd-global-top";
const title=document.createElement("strong");title.textContent=pageNames[pathname];
const home=document.createElement("a");home.href="/portal";home.textContent="返回综合态势 →";
top.append(title,home);
const nodes=[...document.body.childNodes];
document.body.append(root);root.append(aside,content);content.append(top);
for(const n of nodes)content.append(n);
const activeByPath={"/base":"base","/business":"rules","/insights":"insights","/cctv":"cctv","/monitor":"monitor","/admin":"admin","/geo":"insights","/face-lab":"insights","/recognition-results":"recognition","/anime-scenes":"anime"};
const active=aside.querySelector('[data-route="'+activeByPath[pathname]+'"]');
if(active){active.classList.add("active");active.setAttribute("aria-current","page")}
const token=sessionStorage.getItem("zd_access_token");
if(!token){location.replace("/login");return}
fetch("/api/auth/me",{headers:{Authorization:"Bearer "+token}})
.then(r=>r.ok?r.json():null).then(actor=>{
 if(!actor){location.replace("/login");return}
 fetch("/api/insights/overview?days=7",{headers:{Authorization:"Bearer "+token}})
 .then(resp=>{if(resp.ok){
   for(const x of deferredLinks){
     if(x.el.dataset.researchOnly&&actor.level!==1)continue;
     // Original insertion point may be removed while pages initialize; restoring
     // against the stored next sibling maintains the same business order.
     if(x.after&&x.after.parentNode===x.parent)x.parent.insertBefore(x.el,x.after);
     else x.parent.appendChild(x.el);
   }
 }else if(resp.status===410){
   top.querySelector('a[href="/portal"]')?.setAttribute("href","/real");
 }}).catch(()=>{});
}).catch(()=>{aside.querySelectorAll("[data-research-only]").forEach(a=>a.remove())});
})();
