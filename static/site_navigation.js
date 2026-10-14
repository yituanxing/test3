/* A consistent navigation frame for the actual administrator, CCTV and
   device pages. These are working modules, not cards on a mock homepage. */
(function(){
"use strict";
if(!["/admin","/cctv","/monitor"].includes(location.pathname))return;
const page={
 "/admin":{title:"场所与权限管理",active:"admin",subtitle:"维护四级区域、摄像机与授权账户"},
 "/cctv":{title:"视频检测证据",active:"cctv",subtitle:"检查真实研究帧、检测结果和人工审核记录"},
 "/monitor":{title:"设备与采集",active:"monitor",subtitle:"连接现场视频源、查看采集与服务状态"}
}[location.pathname];
const css=document.createElement("style");
css.textContent=`
body{margin:0!important}.zd-global-shell{display:grid;grid-template-columns:235px minmax(0,1fr);min-height:100vh}
.zd-global-nav{box-sizing:border-box;position:sticky;top:0;left:0;height:100vh;z-index:40;background:#122d49;color:#e8f0f7;padding:24px 13px;display:flex;flex-direction:column;font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif}
.zd-global-brand{font-size:17px;font-weight:800;display:flex;align-items:center;gap:11px;padding:0 10px 30px;color:white}
.zd-global-brand span:first-child{width:43px;height:43px;border-radius:13px;background:#418fe0;display:grid;place-items:center;color:white;font-weight:900}
.zd-global-brand small{display:block;color:#91adc6;font-size:11px;font-weight:400}
.zd-global-nav label{display:block;padding:9px 13px;color:#809eb7;font-size:11px;letter-spacing:.1em;margin-top:9px}
.zd-global-nav a{box-sizing:border-box;display:block;margin:2px 0;border-radius:9px;color:#d7e8f5!important;background:transparent;text-decoration:none!important;font-size:14px;padding:13px 16px!important;font-weight:500}
.zd-global-nav a.active,.zd-global-nav a:hover{background:#245274!important;color:white!important;text-decoration:none!important}
.zd-global-nav a.active{box-shadow:inset 3px 0 0 #65b5fa;font-weight:800}
.zd-global-nav>p{border-top:1px solid #34566e;font-size:11px;padding:16px 12px;margin-top:auto;color:#a6bbcc}
.zd-global-body{min-width:0}
.zd-global-top{background:#fff;border-bottom:1px solid #dfe8f0;padding:17px 34px;color:#263d54;display:flex;justify-content:space-between;gap:18px;align-items:center;font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif}
.zd-global-top b{font-size:17px}.zd-global-top span{color:#8392a3;font-size:12px}
.zd-global-body>header{display:none!important}
.zd-global-body>main{max-width:1320px!important;margin:0 auto!important;padding:24px 32px 65px!important}
.zd-global-body>.wrap{max-width:1320px!important;margin:auto!important}
@media(max-width:700px){.zd-global-shell{display:block}.zd-global-nav{height:auto;position:static;display:flex;flex-wrap:wrap;flex-direction:row;padding:10px}.zd-global-brand{width:100%;padding:5px 8px}.zd-global-nav label,.zd-global-nav>p{display:none}.zd-global-nav a{font-size:12px;padding:8px 10px!important}.zd-global-top{padding:10px 16px}.zd-global-top span{display:none}.zd-global-body>main{padding:15px!important}}
`;
document.head.append(css);
const root=document.createElement("div");root.className="zd-global-shell";
const aside=document.createElement("aside");aside.className="zd-global-nav";
aside.innerHTML=`<div class="zd-global-brand"><span>ZD</span><span>视频空间分析<small>演示与管理系统</small></span></div>
 <label>业务功能</label>
 <a href="/real#overview">◈ &nbsp;系统总览</a>
 <a href="/real#video">▣ &nbsp;视频与检测</a>
 <a href="/real#space">◎ &nbsp;空间活动</a>
 <a href="/real#alerts">♢ &nbsp;活动提醒</a>
 <label>系统管理</label>
 <a data-active="monitor" href="/monitor">▤ &nbsp;设备与采集</a>
 <a data-active="admin" href="/admin">⚙ &nbsp;场所与权限</a>
 <a data-active="cctv" href="/cctv">▥ &nbsp;视频证据审核</a>
 <a href="/real#acceptance">☑ &nbsp;功能验收</a>
 <p>本地演示 · 权限由服务器验证</p>`;
const right=document.createElement("div");right.className="zd-global-body";
const bar=document.createElement("div");bar.className="zd-global-top";
bar.innerHTML="<b>"+page.title+"</b><span>"+page.subtitle+"</span>";
const nodes=[...document.body.childNodes];document.body.append(root);root.append(aside,right);right.append(bar);
for(const node of nodes)right.append(node);
const formerHeader=right.querySelector("header");
if(formerHeader)formerHeader.style.display="none";
const active=aside.querySelector('a[data-active="'+page.active+'"]');
if(active){active.classList.add("active");active.setAttribute("aria-current","page")}
const token=sessionStorage.getItem("zd_access_token");
if(token){
 fetch("/api/auth/me",{headers:{Authorization:"Bearer "+token}})
 .then(r=>r.ok?r.json():null).then(user=>{
  if(!user||user.level!==1){
   aside.querySelectorAll('a[href="/real#video"],a[href="/real#space"],a[href="/real#alerts"],a[href="/real#acceptance"]').forEach(a=>{a.hidden=true;a.style.display="none"});
  }
 }).catch(()=>{});
}
})();
