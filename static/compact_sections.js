/* Presentation-only workspaces: preserve API calls, original DOM IDs and permissions.
 * A business page exposes one focused task at a time instead of 8 stacked
 * engineering panels. Hash deep links select the tab containing a target.
 */
(() => {
  "use strict";
  const path = location.pathname;
  const groups = {
    "/business": {
      before: ".layout", card: ".layout > section.panel",
      titles: [
        ["alerts", "预警处理", [0,3], "规则命中与人工核查"],
        ["rules", "规则配置", [1,2], "参数、启停与实例"],
        ["evidence", "业务依据", [4,5,6], "归属、到访与证据"],
        ["history", "审计记录", [7], "修改与复核历史"]
      ]
    },
    "/base": {
      before: ".panel", card: "main .panel",
      titles: [
        ["people", "人员底库", [0,1], "生成、归并与发布"],
        ["maintenance", "底库维护", [2,3], "定期矫正与版本差异"],
        ["versions", "版本审计", [4,5], "历史批次及审核记录"]
      ]
    },
    "/admin": {
      before: ".grid", card: ".grid > section.panel",
      titles: [
        ["places", "辖区与场所", [0,1], "行政区域、场所管理"],
        ["devices", "视频源与摄像机", [2,3,4], "设备绑定与内部位置"],
        ["statistics", "区域统计", [5], "各辖区的视频指标"],
        ["accounts", "账户权限", [6,7], "账号角色与操作审计"]
      ]
    },
    "/monitor": {
      before: ".grid", card: ".grid > section.card",
      titles: [
        ["overview", "采集概览", [0,1,2,3,4], "运行状态与检测记录"],
        ["capture", "持续采集", [5,6,7], "国标、采集任务与采集明细"],
        ["proof", "验证依据", [8], "证据口径及测试说明"]
      ]
    }
  };
  function style() {
    const s=document.createElement("style");
    s.textContent=`
    .zd-workspace-tabs{position:sticky;top:0;z-index:8;display:flex;flex-wrap:wrap;gap:8px;
      align-items:center;margin:13px 0 17px;padding:9px;background:#fff;border:1px solid #dfe7f2;
      box-shadow:0 6px 20px #1a385c13;border-radius:12px}
    .zd-workspace-tabs button{background:#f4f7fb;color:#344d69;border:1px solid transparent;
      box-shadow:none;padding:10px 13px;margin:0;border-radius:9px;line-height:1.35;
      min-width:135px;text-align:left;cursor:pointer;font:inherit}
    .zd-workspace-tabs button strong{display:block;font-size:14px}
    .zd-workspace-tabs button small{display:block;color:#718397;font-size:11px;margin-top:3px}
    .zd-workspace-tabs button[aria-selected="true"]{background:#245da6;color:#fff;border-color:#245da6}
    .zd-workspace-tabs button[aria-selected="true"] small{color:#d8eaff}
    .zd-compact-hidden{display:none!important}
    .zd-workspace-tabs + .layout, .zd-workspace-tabs + .grid{margin-top:0}
    @media(max-width:750px){.zd-workspace-tabs{position:relative;top:auto;flex-wrap:nowrap;
      overflow-x:auto}.zd-workspace-tabs button{min-width:150px;flex-shrink:0}}
    `;
    document.head.append(s);
  }
  const params=new URLSearchParams(location.search);
  const hash=decodeURIComponent(location.hash.replace(/^#/,""));
  let titles=[], targets=[], parentNode=null, insertBefore=null, defaultGroup=null;
  if (path==="/insights") {
    // Group each named business area, including the multiple cards/tables inside it.
    const main=document.querySelector("main.container");
    if(!main)return;
    const headings=[...main.querySelectorAll(":scope > .section-title")];
    if(headings.length!==3)return;
    titles=[
      ["situation","综合分析","场所统计与趋势"],
      ["search","人员检索","按编号查询完整活动"],
      ["real","视频统计","独立真实检测"]
    ];
    targets=headings.map((heading)=>{
      const els=[heading];
      for(let next=heading.nextElementSibling;next && !next.classList.contains("section-title")
          && !next.classList.contains("foot");next=next.nextElementSibling)els.push(next);
      return els;
    });
    parentNode=main;
    insertBefore=headings[0];
    defaultGroup="search";
  }else if (groups[path]) {
    const cfg=groups[path];
    targets=[...document.querySelectorAll(cfg.card)].map(el=>[el]);
    const intended=cfg.titles;
    if(targets.length!==intended.flatMap(x=>x[2]).length)return;
    if(cfg.titles.some(x=>x[2].some(i=>!targets[i])))return;
    titles=intended.map(([id,label,indices,hint])=>[id,label,hint,indices]);
    const anchor=document.querySelector(cfg.before);
    if(!anchor)return;
    parentNode=anchor.parentNode;
    insertBefore=anchor;
    defaultGroup=titles[0][0];
  }else return;

  function elementsFor(t) {
    if(path==="/insights")return targets[titles.indexOf(t)];
    return t[3].flatMap(i=>targets[i]);
  }
  // Existing role-based hidden elements remain hidden: this script cannot grant access.
  function initialTab() {
    const fromQuery=params.get("section");
    if(titles.some(t=>t[0]===fromQuery))return fromQuery;
    if(titles.some(t=>t[0]===hash))return hash;
    if(hash) {
      const found=titles.find(t=>elementsFor(t).some(e=>e.id===hash||e.querySelector?.('#'+CSS.escape(hash))));
      if(found)return found[0];
    }
    return defaultGroup;
  }
  const nav=document.createElement("nav");
  nav.className="zd-workspace-tabs";
  nav.setAttribute("aria-label","本页业务分区");
  const controls=[];
  const active=initialTab();
  style();
  function show(id,writeHistory=false) {
    for(const t of titles){
      for(const el of elementsFor(t))el.classList.toggle("zd-compact-hidden",t[0]!==id);
    }
    for(const c of controls)c.setAttribute("aria-selected",String(c.dataset.tab===id));
    if(writeHistory){
      const url=new URL(location.href);
      url.hash=id;
      history.replaceState(null,"",url.pathname+url.search+url.hash);
      // Restore focus to the tab bar when switching from a scrolled long page.
      nav.scrollIntoView({block:"nearest",behavior:"instant"});
    }
  }
  for(const [id,label,hint] of titles){
    const b=document.createElement("button");
    b.type="button";
    b.dataset.tab=id;
    b.setAttribute("role","tab");
    b.innerHTML="<strong></strong><small></small>";
    b.querySelector("strong").textContent=label;
    b.querySelector("small").textContent=hint;
    b.addEventListener("click",()=>show(id,true));
    controls.push(b);
    nav.append(b);
  }
  parentNode.insertBefore(nav,insertBefore);
  show(active);
  // Hash links generated elsewhere should reveal rather than jump into hidden panels.
  window.addEventListener("hashchange",()=>show(initialTab()));
})();
