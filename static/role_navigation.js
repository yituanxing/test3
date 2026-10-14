/* UI-only affordance policy; real authorization is enforced by the API.
   Always use a fresh authenticated /api/auth/me response, never cached roles. */
(function(){
 "use strict";
 function suppress(element){
  // Native [hidden] can be overridden by authored CSS display:flex/grid.
  // Use !important so the same restriction works across all six legacy UIs.
  element.hidden=true;
  element.style.setProperty('display','none','important');
 }
 function hide(selector){document.querySelectorAll(selector).forEach(suppress)}
 function apply(actor){
  if(!actor||actor.level===1)return;
  hide('a[href="/base"],a[href="/business"],a[href="/portal?tab=acceptance"],nav a[href="/"]');
  document.querySelectorAll('a[href="/cctv#epflResearchPanel"]').forEach(a=>{
   a.href='/cctv#caviarImport';a.textContent='真实研究视频（只读）';
  });
  if(location.pathname==='/cctv'){
   hide('.jumpnav a[href="#epflResearchPanel"]');
   for(const id of ('epflResearchPanel','process')){
    const el=document.getElementById(id);if(el)suppress(el);
   }
  }
  if(location.pathname==='/insights'){
   const e=document.getElementById('seed');if(e)suppress(e);
  }
  if(location.pathname==='/monitor'){
   for(const id of ('videoButton','syncButton')){
    const e=document.getElementById(id);if(e)suppress(e);
   }
  }
 }
 function gate(actor,feature){
  apply(actor);if(actor&&actor.level===1)return false;
  const main=document.querySelector('main');
  if(main)main.innerHTML='<section class="panel" style="max-width:760px;margin:36px auto;padding:24px">'+
   '<h2>当前账号仅能使用获授权的辖区功能</h2>'+
   '<p>「'+feature+'」是州级管理员的合成人员业务实验。当前账号无该页面的数据或操作权限。</p>'+
   '<p><a href="/real">返回真实数据工作台</a></p></section>';
  return true;
 }
 window.zdApplyRoleNavigation=apply;
 window.zdGateStateOnlyPage=gate;
})();
