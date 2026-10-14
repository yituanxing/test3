/* ZD operational site: one application with separate functional modules.
   The source video and recorded evidence must exist; no fabricated statistics. */
(function(){
"use strict";
const byId=id=>document.getElementById(id);
const main=document.querySelector("main");
if(!main||!byId("researchActivity")||!byId("realSceneAggregation"))return;
const modules={
 video:byId("researchActivity"),space:byId("realSceneAggregation"),
 alerts:byId("demoAlerts"),acceptance:byId("excelAcceptance"),
 rules:byId("excelRuleShowcase"),live:byId("liveEvidence"),
 audit:byId("researchAudit"),oldPlayer:byId("localVideoPlayback")
};
const app=document.createElement("div");app.className="zd-shell";app.id="zdApp";
const sidebar=document.createElement("aside");sidebar.className="zd-sidebar";
sidebar.innerHTML=`<div class="zd-brand"><span class="zd-logo">ZD</span><span>视频空间分析<small>演示与管理系统</small></span></div>
 <div class="zd-menu-heading">业务功能</div>
 <button class="zd-nav" data-panel="overview"><span>◈</span> 系统总览</button>
 <button class="zd-nav" data-panel="video"><span>▣</span> 视频与检测</button>
 <button class="zd-nav" data-panel="space"><span>◎</span> 空间活动</button>
 <button class="zd-nav" data-panel="alerts"><span>♢</span> 活动提醒</button>
 <div class="zd-menu-heading">系统管理</div>
 <a class="zd-nav" href="/monitor"><span>▤</span> 设备与采集</a>
 <a class="zd-nav" href="/admin"><span>⚙</span> 场所与权限</a>
 <a class="zd-nav" href="/cctv"><span>▥</span> 检测证据审核</a>
 <button class="zd-nav" data-panel="acceptance"><span>☑</span> 功能验收</button>
 <div class="zd-sidebar-bottom"><span class="zd-status-light"></span> 本地运行 <a href="#" id="logout">退出</a></div>`;
const work=document.createElement("div");work.className="zd-work";
const top=document.createElement("div");top.className="zd-top";
top.innerHTML='<strong id="moduleTopTitle">系统总览</strong><span>真实录像技术原型 · 本地访问</span>';
app.append(sidebar,work);
const originalHeader=document.querySelector("body>header");
if(originalHeader)originalHeader.remove();
document.body.insertBefore(app,main);
work.append(top,main);

function panel(id){
 const node=document.createElement("section");node.className="zd-panel";node.dataset.view=id;
 return node;
}
const home=panel("overview");
home.innerHTML=`<div class="zd-welcome"><span class="zd-eyebrow">ZD · 实验室双机视频分析</span>
 <h1>从两台摄像机，看到空间中的活动</h1>
 <p>看真实录像、看人体检测、看短时位置变化，再按规则查看活动提醒。每个功能都有独立入口。</p>
 <div class="zd-welcome-actions"><button type="button" data-go="video">进入视频分析 →</button><button type="button" data-go="space" class="zd-ghost">查看空间活动</button></div>
 </div>
 <div class="zd-main-grid"><div class="zd-primary-card">
  <div class="zd-card-title"><b>双摄像机真实录像</b><span>历史科研录像 · 同一实验室</span></div>
  <video id="overviewVideo" playsinline controls preload="none"></video>
  <div class="zd-media-status" id="overviewVideoState">正在检查是否安装研究录像…</div>
  <div class="zd-live-caption" id="overviewSample">开始播放后，将在附近的已检测采样帧显示观测记录。</div>
 </div>
 <div class="zd-summary-card"><h2>视频分析结果</h2><p>下列值从已导入的真实录像检测记录读取，不使用固定展示数字。</p>
  <div class="zd-summary-grid" id="overviewKpis"><div><b>—</b><span>正在读取视频证据…</span></div></div>
  <div class="zd-success-note" id="overviewEvidenceStatus">正在连接本地研究数据库…</div>
  <button class="zd-inline-link" data-go="video" type="button">查看每一次检测及源帧 →</button>
 </div></div>
 <div class="zd-section-label">按业务顺序操作</div>
 <div class="zd-action-grid">
  <button data-go="video"><span class="zd-step">01</span><b>视频与检测</b><small>双机录像、配对记录、研究证据</small><em>进入 →</em></button>
  <button data-go="space"><span class="zd-step">02</span><b>空间活动</b><small>短轨迹、室内A/B分区、动态热区</small><em>进入 →</em></button>
  <button data-go="alerts"><span class="zd-step">03</span><b>活动提醒</b><small>区域规则、阈值命中、来源帧</small><em>进入 →</em></button>
 </div>
 <div class="zd-bottom-info">数据说明：EPFL双机拍摄同一真实实验室；局部区域为示意划分，演示提醒不代表实际警情。现场摄像机尚需单独接入。</div>`;
const video=panel("video");
video.innerHTML=`<div class="zd-module-header"><span class="zd-eyebrow">业务模块 / 视频检测</span><h1>视频与检测</h1>
<p>先看连续双摄像机录像，然后核对从原视频得到的检测和短时间匹配。没有视频时会直接提示缺少文件。</p></div>`;
const space=panel("space");
space.innerHTML='<div class="zd-module-header"><span class="zd-eyebrow">业务模块 / 空间分析</span><h1>空间活动</h1><p>不同格子的观测数量、经过核验的短轨迹和室内A/B区域活动，支持选择时刻和调整规则。</p></div>';
const alerts=panel("alerts");
alerts.innerHTML='<div class="zd-module-header"><span class="zd-eyebrow">业务模块 / 活动提醒</span><h1>活动提醒</h1><p>查看实际视频活动触发的演示规则，或进入空间模块调整阈值和区域位置。提醒不会被标记成现实风险。</p><button data-go="space" type="button" class="zd-primary-action">调整空间规则 →</button></div>';
const acceptance=panel("acceptance");
acceptance.innerHTML='<div class="zd-module-header"><span class="zd-eyebrow">系统管理 / 验收</span><h1>功能验收与技术资料</h1><p>原Excel的23项功能状态、独立算法测试和来源证据。用户演示无需在这里操作。</p></div>';
const live=panel("live");
live.innerHTML='<div class="zd-module-header"><h1>现场设备事件</h1><p>此模块读取连接到本机Frigate的摄像机事件，独立于EPFL历史研究录像。</p></div>';
for(const id of Object.keys(modules)){if(id!=="oldPlayer"&&modules[id])modules[id].remove()}
const videoPlayer=byId("videoEvidence");
if(videoPlayer)video.prepend(videoPlayer);
video.append(modules.video);
space.append(modules.space);
alerts.append(modules.alerts);
acceptance.append(modules.acceptance,modules.rules,modules.audit);
live.append(modules.live);
if(modules.live)modules.live.open=true;
// Preserve the legacy local upload controls offstage so backward-compatible
// event listeners never crash, but never expose a second duplicated player.
const hiddenLegacy=document.createElement("div");hiddenLegacy.hidden=true;
if(modules.oldPlayer)hiddenLegacy.append(modules.oldPlayer);
main.replaceChildren(home,video,space,alerts,acceptance,live,hiddenLegacy);
for(const old of document.querySelectorAll(".zd-panel .stage-head"))old.remove();
const views={overview:home,video,space,alerts,acceptance,live};
const headings={overview:"系统总览",video:"视频与检测",space:"空间活动",alerts:"活动提醒",acceptance:"功能验收",live:"现场设备事件"};
const hashMapping={
 researchActivity:"video",videoEvidence:"video",realSceneAggregation:"space",
 demoAlerts:"alerts",excelRuleShowcase:"acceptance",excelAcceptance:"acceptance",
 technicalArea:"acceptance",liveEvidence:"live"
};
function selectedFromHash(){
 const requested=decodeURIComponent(location.hash.replace(/^#/,""));
 return views[requested]?requested:(hashMapping[requested]||"overview");
}
function showPanel(name,writeHash){
 const id=views[name]?name:"overview";
 const role=window.zdActor;
 if(role&&role.level!==1&&["video","space","alerts","acceptance"].includes(id))return showPanel("live",writeHash);
 Object.entries(views).forEach(([key,value])=>{value.hidden=key!==id});
 document.querySelectorAll(".zd-nav[data-panel]").forEach(b=>{
  b.classList.toggle("selected",b.dataset.panel===id);
  b.setAttribute("aria-current",b.dataset.panel===id?"page":"false");
 });
 byId("moduleTopTitle").textContent=headings[id];
 if(writeHash)history.replaceState(null,"","#"+id);
 if(id!=="overview")byId("overviewVideo").pause();
 if(id!=="video")byId("videoClip").pause();
 scrollTo({top:0,behavior:"instant"});
}
window.zdShowPanel=showPanel;
window.addEventListener("hashchange",()=>showPanel(selectedFromHash(),false));
document.body.addEventListener("click",e=>{
 const el=e.target.closest("[data-panel],[data-go]");
 if(!el||el.tagName==="A")return;
 showPanel(el.dataset.panel||el.dataset.go,true);
});
showPanel(selectedFromHash(),false);
const logoff=byId("logout");
if(logoff)logoff.onclick=async event=>{
 event.preventDefault();
 try{await api("/api/auth/logout","POST")}catch(e){}
 sessionStorage.removeItem("zd_access_token");
 location.replace("/login");
};

// Mirror actual imported counts; empty and error states stay empty rather
// than filling a mock total. The original APIs remain the sole data source.
function updateKpis(){
 const cards=[...byId("activityStats").querySelectorAll(".card")];
 const target=byId("overviewKpis");
 if(!cards.length)return;
 target.innerHTML=cards.slice(0,4).map(card=>{
  const label=card.querySelector(".small")?.textContent||"视频观测";
  const value=card.querySelector("strong")?.textContent||"—";
  return '<div><b>'+value+'</b><span>'+label+'</span></div>';
 }).join("");
 byId("overviewEvidenceStatus").textContent="已读取研究视频证据。查看‘视频与检测’核对源录像帧。";
}
new MutationObserver(updateKpis).observe(byId("activityStats"),{childList:true,subtree:true});
updateKpis();

function syncFromVideo(player){
 if(typeof sceneData==="undefined")return;
 if(typeof sceneData==="undefined"||!sceneData?.frame_cells?.length)return;
 const fps=Number(window.clipFPS||clipFPS)||25;
 const estimated=(60+player.currentTime)*fps;
 let best=-1,min=Infinity;
 sceneData.frame_cells.forEach((row,i)=>{
  const delta=Math.abs(row.video_frame-estimated);
  if(delta<min){min=delta;best=i}
 });
 if(best<0||min>fps*.65){
  byId("overviewSample").textContent="该画面附近没有已保存的检测采样帧；不补造识别结果。";
  return;
 }
 const sample=sceneData.frame_cells[best];
 byId("overviewSample").textContent="当前画面附近：源视频第 "+sample.video_frame+
  " 帧，双机人体配对观测 "+sample.paired_detection_count+" 次（非独立人数）。";
 if(sceneFrameIndex!==best+1){sceneFrameIndex=best+1;scenePaint()}
}
let lastSync=-1;
byId("overviewVideo").addEventListener("timeupdate",e=>{
 if(Math.abs(e.target.currentTime-lastSync)<.25)return;
 lastSync=e.target.currentTime;syncFromVideo(e.target);
});
const primary=byId("videoClip");
if(primary)primary.addEventListener("timeupdate",e=>syncFromVideo(e.target));
function attachClipBlob(blob){
 if(!(blob instanceof Blob)||blob.size<100000)throw Error("录像文件无效");
 const link=URL.createObjectURL(blob);
 // Reuse the single controlled source in both dashboard and analysis pages.
 if(typeof clipObjectURL!=="undefined"&&clipObjectURL)URL.revokeObjectURL(clipObjectURL);
 clipObjectURL=link;
 const preview=byId("overviewVideo");
 const video=byId("videoClip");
 video.onloadedmetadata=()=>{
  if(video.duration<36||video.duration>40||video.videoWidth<1200){
   byId("videoClipStatus").textContent="视频规格不符，请使用官方38秒双机节选。";
   byId("overviewVideoState").textContent="视频规格不符，未用于演示";return;
  }
  clipReady=true;video.hidden=false;byId("videoClipPlaceholder").hidden=true;
  byId("videoClipStatus").textContent="双摄像机真实连续录像 · 已加载 "+video.duration.toFixed(1)+" 秒";
  byId("overviewVideoState").textContent="已加载真实连续双机录像 · 点击播放";
 };
 video.onerror=()=>{byId("videoClipStatus").textContent="视频解码失败";byId("overviewVideoState").textContent="视频解码失败，请检查H.264支持"};
 video.src=link;video.load();
 preview.src=link;preview.load();
}
async function loadBundledClip(){
 try{
  const status=await api("/api/real/showcase-media/status");
  if(!status.ready){
   byId("overviewVideoState").textContent="未安装连续录像。请在‘视频与检测’选择随包附带的38秒双机MP4。";
   return;
  }
  const response=await fetch("/api/real/showcase-media/video",{headers:{Authorization:"Bearer "+sessionStorage.getItem("zd_access_token")}});
  if(!response.ok)throw Error("视频请求 HTTP "+response.status);
  attachClipBlob(await response.blob());
 }catch(e){
  byId("overviewVideoState").textContent="录像加载失败："+e.message+"。可在视频分析页手动选择MP4。";
 }
}
byId("videoClipPicker")?.addEventListener("change",()=>{
 const file=byId("videoClipPicker").files?.[0];
 if(file)byId("overviewVideoState").textContent="正在加载选取的视频…";
});
// Existing picker already validates filename and video dimensions.
const mainVideo=byId("videoClip");
if(mainVideo)new MutationObserver(()=>{
 const source=mainVideo.getAttribute("src");
 if(source&&source.startsWith("blob:")&&!byId("overviewVideo").src.endsWith(source)){
  byId("overviewVideo").src=source;byId("overviewVideo").load();
 }
}).observe(mainVideo,{attributes:true,attributeFilter:["src"]});
api("/api/auth/me").then(actor=>{
 window.zdActor=actor;
 if(actor.level!==1){
  sidebar.querySelectorAll('[data-panel="video"],[data-panel="space"],[data-panel="alerts"],[data-panel="acceptance"]').forEach(el=>el.hidden=true);
  byId("overviewKpis").innerHTML='<div><b>—</b><span>研究视频仅向研究管理员开放</span></div>';
  byId("overviewEvidenceStatus").textContent="当前账号仅显示授权辖区数据；可进入设备与采集页面。";
  showPanel("live",false);
 }else loadBundledClip();
}).catch(e=>{byId("overviewVideoState").textContent="会话检查失败，请重新登录"});
})();
