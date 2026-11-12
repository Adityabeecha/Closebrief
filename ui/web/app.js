const $=id=>document.getElementById(id);
let currentPeriod=null, currentFacts=[], detailFact=null, activeDatasetId=null, searchQuery="", datasetsCache=[];
// Reopen the dataset this browser was last looking at (the server ignores it if it no longer exists).
try{const v=+localStorage.getItem("cb-ds");if(v)activeDatasetId=v;}catch{}
/* auth state */
// signedIn: this browser has a session cookie (HttpOnly; the page never holds the token).
let sb=null, signedIn=false, authMode="signin", currentUser={email:"local@dev",role:"admin"};

/* ---------- theme (dark mode) ---------- */
const SUN='<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON='<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
function applyTheme(t){
  document.documentElement.setAttribute("data-theme",t);
  const b=$("theme-btn");if(b)b.innerHTML=t==="dark"?SUN:MOON;
}
function initTheme(){
  const saved=localStorage.getItem("cb-theme");
  const t=saved||(matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
  applyTheme(t);
}
function toggleTheme(){
  const t=document.documentElement.getAttribute("data-theme")==="dark"?"light":"dark";
  localStorage.setItem("cb-theme",t);applyTheme(t);
  // Chart.js bakes colors at build time — rebuild the open charts.
  if(detailFact&&$("v-detail").style.display!=="none"){
    const cd=detailFact.chart_data||{};
    renderTrendChart(cd.trend||[],detailFact.unit);
    renderBvaChart(cd.budget_vs_actual,detailFact.unit);
    renderBridgeChart(cd.variance_bridge,detailFact.deltas.budget_var_abs,detailFact.unit);
  }
  if($("v-costs")&&$("v-costs").style.display!=="none"&&costsData)loadCosts();
}
initTheme();

/* ---------- export (PDF / PPTX) ---------- */
/* Fixed brand colors for standalone exports, which can't read the app's CSS
   tokens at runtime. Keep in sync with --accent / --red in :root. */
const BRAND={green:"#047857",greenDark:"#34d399",red:"#c8281c"};
function toggleExport(e){e.stopPropagation();$("export-menu").classList.toggle("open");}
function reportDelta(label,v,downGood,text){
  const p=text!==undefined?text:pct(v);if(p===null||v==null)return`<span class="rd flat">${label} –</span>`;
  const good=downGood?v<0:v>0,col=v===0?"#596579":good?"#067647":BRAND.red,bg=v===0?"#f1f4f8":good?"#e8f8ef":"#fdeeed";
  return`<span class="rd" style="color:${col};background:${bg}">${label} ${p}</span>`;
}
function buildReportHTML(facts){
  const withData=facts.filter(f=>f.has_data);
  const anom=withData.filter(f=>f.is_anomaly).length;
  const mover=withData.filter(f=>f.deltas&&f.deltas.budget_var_pct!=null).sort((a,b)=>Math.abs(b.deltas.budget_var_pct)-Math.abs(a.deltas.budget_var_pct))[0];
  const cards=withData.map(f=>{
    const dg=f.direction_good==="down",col=catColor(f.category)||"#8a948c";
    const spark=(f.chart_data&&f.chart_data.trend)?sparkline(f.chart_data.trend,f.direction_good,150,40,f.unit):"";
    const src=(f.sources||[]).map(s=>`<span class="src">${esc(s.title||s.id)}</span>`).join("");
    return`<div class="rcard" style="border-left:3px solid ${col}">
      <div class="rc-top"><span class="rc-dot" style="background:${col}"></span><span class="rc-name">${esc(f.metric)}</span>
        <span class="rc-cat">${esc(f.category||"")}</span></div>
      <div class="rc-vrow"><span class="rc-val">${fmt(f.value,f.unit)}</span>${spark}</div>
      <div class="rc-deltas">${reportDelta(priorPeriodLabel(),f.deltas.mom_pct,dg,deltaText(f,"mom"))}${reportDelta("vs plan",f.deltas.budget_var_pct,dg,deltaText(f,"budget"))}${f.is_anomaly?'<span class="rd" style="color:#b42e2c;background:#faeeed">ANOMALY</span>':""}</div>
      <div class="rc-narr">${f.narrative?esc(f.narrative):"<i>No narrative generated for this metric.</i>"}</div>
      ${f.faithfulness==="failed"?'<div class="rd" style="color:#b42e2c">Numbers need review — unverified narrative</div>':""}
      ${src?`<div class="rc-src">${src}</div>`:""}
    </div>`;
  }).join("");
  const today=new Date().toLocaleDateString(undefined,{year:"numeric",month:"long",day:"numeric"});
  return`<!doctype html><html><head><meta charset="utf-8"><title>Closebrief Report ${esc(currentPeriod||"")}</title>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    :root{--green2:${BRAND.green};--red:${BRAND.red};--mut2:#8a948c}
    body{font-family:'Schibsted Grotesk',system-ui,-apple-system,Segoe UI,sans-serif;color:#16211b;padding:28px 32px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
    @font-face{font-family:'Schibsted Grotesk';font-weight:400 900;src:url(/vendor/fonts/schibsted-grotesk.woff2) format('woff2')}
    @font-face{font-family:'Red Hat Mono';font-weight:400 700;src:url(/vendor/fonts/redhat-mono.woff2) format('woff2')}
    .rhead{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2.5px solid ${BRAND.green};padding-bottom:14px;margin-bottom:18px}
    .rhead .mark{display:inline-flex;align-items:center;gap:9px}
    .rhead .mk{width:26px;height:26px;border-radius:8px;background:${BRAND.green};display:inline-flex;align-items:center;justify-content:center}
    .rhead h1{font-family:'Schibsted Grotesk',system-ui,sans-serif;font-size:20px;font-weight:600;letter-spacing:-.005em}
    .rhead .sub{font-size:12px;color:#5c6a62;margin-top:2px}
    .rhead .r{text-align:right;font-size:12px;color:#5c6a62;line-height:1.6}
    .rsummary{display:flex;gap:26px;background:#f4f7f4;border:1px solid #e2e8e2;border-radius:8px;padding:13px 18px;margin-bottom:20px}
    .rsummary div{font-size:12px;color:#5c6a62;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
    .rsummary b{display:block;font-family:'Red Hat Mono',Menlo,monospace;font-size:20px;color:#16211b;font-weight:500;margin-top:3px;text-transform:none;letter-spacing:0}
    .rgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
    .rcard{border:1px solid #e2e8e2;border-radius:12px;padding:15px 16px;break-inside:avoid}
    .rc-top{display:flex;align-items:center;gap:7px;margin-bottom:8px}
    .rc-dot{width:8px;height:8px;border-radius:50%;flex:none}
    .rc-name{font-weight:700;font-size:14px}
    .rc-cat{margin-left:auto;font-size:12px;color:#8a948c;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
    .rc-vrow{display:flex;align-items:flex-end;justify-content:space-between;gap:10px;margin-bottom:9px}
    .rc-val{font-family:'Red Hat Mono',Menlo,monospace;font-size:24px;font-weight:500;letter-spacing:-.02em}
    .rc-deltas{display:flex;gap:7px;margin-bottom:9px}
    .rd{font-size:12px;font-weight:700;padding:3px 8px;border-radius:8px}
    .rd.flat{color:#5c6a62;background:#f1f3f0}
    .rc-narr{font-family:'Schibsted Grotesk',system-ui,sans-serif;font-size:14px;line-height:1.6;color:#33413a}
    .rc-src{margin-top:9px;display:flex;flex-wrap:wrap;gap:5px}
    .src{font-size:9.5px;font-weight:700;color:${BRAND.green};background:#e9f3ee;border:1px solid #c6ddd1;padding:2px 7px;border-radius:4px}
    .rfoot{margin-top:22px;padding-top:12px;border-top:1px solid #e2e8e2;font-size:12px;color:#8a948c;text-align:center}
    @page{margin:14mm}
    @media print{.rcard{break-inside:avoid}}
  </style></head><body>
    <div class="rhead">
      <div class="mark"><span class="mk"><svg width="15" height="15" viewBox="0 0 24 24"><path d="M5 19v-6M12 19V9M19 19V6.5" stroke="#fff" stroke-width="2.5" stroke-linecap="round" fill="none"/></svg></span>
        <div><h1>Closebrief · FP&amp;A Executive Report</h1><div class="sub">Period ${esc(currentPeriod||"—")}</div></div></div>
      <div class="r">Generated ${today}<br>Confidential</div>
    </div>
    <div class="rsummary">
      <div>KPIs tracked<b>${withData.length}</b></div>
      <div>Anomalies<b>${anom}</b></div>
      <div>Top mover vs plan<b style="font-size:14px">${mover?esc(mover.metric)+" ("+(deltaText(mover,"budget")||"–")+")":"—"}</b></div>
    </div>
    <div class="rgrid">${cards||'<p style="color:#8a948c">No KPI data for this period.</p>'}</div>
    <div class="rfoot">Generated by Closebrief · ${today} · Confidential</div>
  </body></html>`;
}
function exportPDF(){
  $("export-menu").classList.remove("open");
  const facts=shownFacts.length?shownFacts:currentFacts;
  const w=window.open("","_blank");
  if(!w){toast("Allow pop-ups to export the PDF report");return;}
  w.document.open();w.document.write(buildReportHTML(facts));w.document.close();
  // Give the window a beat to lay out fonts/sparklines before invoking print.
  setTimeout(()=>{try{w.focus();w.print();}catch(e){}},450);
}
let _pptxLoading=null;
function loadPptx(){
  if(window.PptxGenJS)return Promise.resolve();
  if(_pptxLoading)return _pptxLoading;
  _pptxLoading=new Promise((res,rej)=>{const s=document.createElement("script");
    s.src="/vendor/pptxgen.min.js";s.onload=res;s.onerror=()=>rej(new Error("Could not load PPTX library"));
    document.head.appendChild(s);});
  return _pptxLoading;
}
async function exportPPTX(){
  $("export-menu").classList.remove("open");
  try{
    toast("Building PowerPoint…");
    await loadPptx();
    const facts=shownFacts.filter(f=>f.has_data);
    // Decks are read on projectors and printed: always the light palette.
    const INK="0B1220",TXT="344054",MUT="596579",LINE="E6E9EF",ACC=BRAND.green.slice(1).toUpperCase(),RED=BRAND.red.slice(1).toUpperCase(),OK="067647",AMB="A1450A";
    const F="Segoe UI",M="Consolas";
    const title=currentPeriod?fmtPeriodTitle(currentPeriod):"Close";
    const p=new PptxGenJS();p.layout="LAYOUT_16x9";   // 10 x 5.63in
    const foot=(s,n)=>{s.addShape(p.ShapeType.line,{x:0.6,y:5.15,w:8.8,h:0,line:{color:LINE,width:0.75}});
      s.addText([{text:"close",options:{bold:true}},{text:"brief"}],{x:0.6,y:5.2,w:3,h:0.3,fontSize:10,color:INK,fontFace:F});
      s.addText(`${title} · ${n}`,{x:5.4,y:5.2,w:4,h:0.3,fontSize:10,color:MUT,fontFace:F,align:"right"});};
    // Title slide
    let s=p.addSlide();s.background={color:"FFFFFF"};
    s.addShape(p.ShapeType.rect,{x:0.6,y:1.45,w:0.6,h:0.08,fill:{color:ACC},line:{color:ACC}});
    s.addText(title,{x:0.6,y:1.65,w:8.8,h:0.9,fontSize:38,bold:true,color:INK,fontFace:F});
    s.addText("Variance commentary. Every figure computed in code and checked against the source.",{x:0.6,y:2.55,w:8.8,h:0.5,fontSize:16,color:TXT,fontFace:F});
    s.addText(`${activeDatasetObj&&activeDatasetObj.name?datasetDisplayName(activeDatasetObj.name)+" · ":""}Prepared ${fmtDate(new Date().toISOString())}`,{x:0.6,y:4.6,w:8.8,h:0.4,fontSize:12,color:MUT,fontFace:F});
    s.addText([{text:"close",options:{bold:true}},{text:"brief"}],{x:0.6,y:0.5,w:3,h:0.4,fontSize:16,color:INK,fontFace:F});
    // At a glance: the movers against plan, largest first
    const anom=facts.filter(f=>f.is_anomaly).length,approved=facts.filter(f=>f.review_status==="approved").length;
    const movers=facts.slice().sort((a,b)=>Math.abs((b.deltas&&b.deltas.budget_var_pct)||0)-Math.abs((a.deltas&&a.deltas.budget_var_pct)||0)).slice(0,8);
    s=p.addSlide();s.background={color:"FFFFFF"};
    s.addText("At a glance",{x:0.6,y:0.4,w:8.8,h:0.6,fontSize:24,bold:true,color:INK,fontFace:F});
    s.addText(`${plural(facts.length,"KPI","KPIs")} · ${plural(anom,"anomaly","anomalies")} · ${approved} of ${facts.length} approved`,{x:0.6,y:0.95,w:8.8,h:0.35,fontSize:13,color:MUT,fontFace:F});
    const head=["KPI","Actual","Vs plan",capFirst(priorPeriodLabel()),"Status"].map(t=>({text:t,options:{bold:true,color:MUT,fontSize:11,fontFace:F}}));
    const tone=(f,v)=>v==null||v===0?TXT:((f.direction_good==="down"?v<0:v>0)?OK:RED);
    const body=movers.map(f=>[
      {text:f.metric,options:{color:INK,fontSize:12,fontFace:F}},
      {text:fmt(f.value,f.unit),options:{color:INK,fontSize:12,fontFace:M,align:"right"}},
      {text:deltaText(f,"budget")||"–",options:{color:tone(f,f.deltas.budget_var_pct),fontSize:12,fontFace:M,align:"right"}},
      {text:deltaText(f,"mom")||"–",options:{color:tone(f,f.deltas.mom_pct),fontSize:12,fontFace:M,align:"right"}},
      {text:f.faithfulness==="failed"?"Needs review":f.review_status==="approved"?"Approved":f.narrative?"Draft":"Not drafted",options:{color:f.faithfulness==="failed"?AMB:MUT,fontSize:11,fontFace:F}}]);
    s.addTable([head,...body],{x:0.6,y:1.45,w:8.8,colW:[3.4,1.5,1.2,1.2,1.5],border:{type:"solid",color:LINE,pt:0.75},rowH:0.36});
    foot(s,2);
    // One slide per KPI
    facts.forEach((f,i)=>{
      const s=p.addSlide();s.background={color:"FFFFFF"};
      const state=f.faithfulness==="failed"?["Needs review",AMB]:f.review_status==="approved"?["Approved",OK]:f.narrative?["Draft",MUT]:["Not drafted",MUT];
      s.addText(f.metric,{x:0.6,y:0.4,w:7,h:0.6,fontSize:22,bold:true,color:INK,fontFace:F});
      s.addText(state[0],{x:7.6,y:0.48,w:1.8,h:0.4,fontSize:12,bold:true,color:state[1],fontFace:F,align:"right"});
      s.addText(f.category||"",{x:0.6,y:0.95,w:8.8,h:0.35,fontSize:12,color:MUT,fontFace:F});   // PPTX text is not HTML — no esc()
      s.addText(fmt(f.value,f.unit),{x:0.6,y:1.4,w:5,h:0.9,fontSize:40,bold:true,color:INK,fontFace:M});
      const figs=[["Vs plan",f.deltas.budget_var_pct,"budget"],[capFirst(priorPeriodLabel()),f.deltas.mom_pct,"mom"]];
      figs.forEach(([l,v,key],k)=>{s.addText(l,{x:6.2,y:1.45+k*0.45,w:1.7,h:0.4,fontSize:12,color:MUT,fontFace:F});
        s.addText(deltaText(f,key)||"–",{x:7.9,y:1.45+k*0.45,w:1.5,h:0.4,fontSize:13,bold:true,color:tone(f,v),fontFace:M,align:"right"});});
      s.addText((f.faithfulness==="failed"?"Numbers need review — unverified narrative\n\n":"")+(f.narrative||"No narrative generated."),{x:0.6,y:2.55,w:8.8,h:2.3,fontSize:14,color:TXT,fontFace:F,valign:"top",paraSpaceAfter:6});
      if(f.sources&&f.sources.length)s.addText("Sources: "+f.sources.map(x=>x.title||x.id).join(", "),{x:0.6,y:4.75,w:8.8,h:0.35,fontSize:10,italic:true,color:MUT,fontFace:F});
      foot(s,i+3);
    });
    await p.writeFile({fileName:`Closebrief_${(currentPeriod||"report").replace(/[^0-9A-Za-z_-]/g,"")}.pptx`});
    toast("PowerPoint downloaded");
  }catch(e){toast(e.message||"Export failed");}
}

/* ---------- command palette (Ctrl+K) ---------- */
let cmdkSel=0, cmdkItems=[];
const CI={page:'<path d="M4 4h16v16H4z"/><path d="M4 9h16"/>',metric:'<path d="M3 3v18h18"/><path d="m7 14 4-4 3 3 5-5"/>',
  bolt:'<path d="M13 2 3 14h7l-1 8 10-12h-7z"/>',sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  down:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>'};
function paletteActions(){
  const pages=[...MONTH_TABS.filter(t=>t[0]!=="data"||canReview()).map(([k,l])=>[k,currentPeriod?`${l} · ${fmtPeriod(currentPeriod)}`:l]),
    ...PLACES.filter(([k])=>k!=="admin"||currentUser.role==="admin").filter(([k])=>!["costs","notifications"].includes(k)||currentUser.role==="admin").map(([k,,,l])=>[k,l])];
  const acts=[
    {label:"Draft all commentary",icon:CI.bolt,run:()=>generateAll(),group:"Actions"},
    {label:"Export report (PDF)",icon:CI.down,run:()=>exportPDF(),group:"Actions"},
    {label:"Export report (PowerPoint)",icon:CI.down,run:()=>exportPPTX(),group:"Actions"},
    {label:"Toggle dark mode",icon:CI.sun,run:()=>toggleTheme(),group:"Actions"},
  ];
  const pageItems=pages.map(([v,l])=>({label:l,icon:CI.page,run:()=>requestImportView(v),group:"Pages"}));
  const metricItems=(currentFacts||[]).filter(f=>f.has_data).map(f=>({label:f.metric,sub:f.category||"",icon:CI.metric,
    run:()=>requestImportAction(()=>{nav("dashboard");const fresh=currentFacts.find(x=>x.metric===f.metric);if(!fresh)return;const i=shownFacts.indexOf(fresh);(i>=0)?openDetail(i):(searchQuery="",renderCards(),openDetail(shownFacts.indexOf(fresh)));}),group:"Metrics"}));
  const closeItems=[...allPeriods].reverse().slice(0,12).map(p=>({label:fmtPeriodTitle(p),icon:CI.page,run:()=>selectClose(p),group:"Closes"}));
  return[...acts,...pageItems,...closeItems,...metricItems];
}
function openPalette(){
  const bk=$("cmdk-back");bk.classList.add("open");
  const inp=$("cmdk-input");inp.value="";cmdkSel=0;renderPalette();
  setTimeout(()=>inp.focus(),30);
}
function closePalette(){$("cmdk-back").classList.remove("open");}
function renderPalette(){
  const q=($("cmdk-input").value||"").toLowerCase().trim();
  let all=paletteActions();
  if(q)all=all.filter(it=>it.label.toLowerCase().includes(q)||(it.sub||"").toLowerCase().includes(q));
  cmdkItems=all;if(cmdkSel>=all.length)cmdkSel=Math.max(0,all.length-1);
  const list=$("cmdk-list");
  if(!all.length){list.innerHTML=`<div class="cmdk-empty">No matches for “${esc(q)}”</div>`;return;}
  let html="",lastG=null;
  all.forEach((it,i)=>{
    if(it.group!==lastG){html+=`<div class="cmdk-group">${it.group}</div>`;lastG=it.group;}
    html+=`<div class="cmdk-item${i===cmdkSel?" sel":""}" data-i="${i}" onmousemove="cmdkSel=${i};paintPalette()" onclick="runPalette(${i})">
      <span class="ci-ic"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${it.icon}</svg></span>
      <span>${esc(it.label)}</span>${it.sub?`<small>${esc(it.sub)}</small>`:""}</div>`;
  });
  list.innerHTML=html;
}
function paintPalette(){document.querySelectorAll(".cmdk-item").forEach(el=>el.classList.toggle("sel",+el.dataset.i===cmdkSel));}
function runPalette(i){const it=cmdkItems[i];if(!it)return;closePalette();setTimeout(()=>it.run(),30);}
function paletteKey(e){
  if(e.key==="Escape"){closePalette();return;}
  if(e.key==="ArrowDown"){e.preventDefault();cmdkSel=Math.min(cmdkSel+1,cmdkItems.length-1);paintPalette();scrollSel();}
  else if(e.key==="ArrowUp"){e.preventDefault();cmdkSel=Math.max(cmdkSel-1,0);paintPalette();scrollSel();}
  else if(e.key==="Enter"){e.preventDefault();runPalette(cmdkSel);}
}
function scrollSel(){document.querySelector(".cmdk-item.sel")?.scrollIntoView({block:"nearest"});}

/* ---------- KPI manager ---------- */
let kpimState={selected:[],available:[]};
async function openKpiManager(){
  try{kpimState=await api("/kpis");}catch(e){toast(e.message);return;}
  renderKpim();$("kpi-back").classList.add("open");
}
function renderKpim(){
  const dirOpt=v=>`<option value="up"${v==="up"?" selected":""}>↑ up is good</option><option value="down"${v==="down"?" selected":""}>↓ down is good</option>`;
  const aggOpt=v=>["flow","balance","ratio"].map(a=>`<option value="${a}"${v===a?" selected":""}>${a}</option>`).join("");
  $("kpim-rows").innerHTML=kpimState.selected.length?`
    <div class="kpi-grid hdr" style="grid-template-columns:1.2fr 1.2fr .9fr .7fr .9fr .8fr .3fr"><span>Source metric</span><span>Display name</span><span>Category</span><span>Unit</span><span>Direction</span><span title="How this rolls up to quarters/years">Rollup</span><span></span></div>`
    +kpimState.selected.map((k,i)=>`<div class="kpi-grid" data-i="${i}" style="grid-template-columns:1.2fr 1.2fr .9fr .7fr .9fr .8fr .3fr">
      <span style="font-size:12px;color:var(--mut);overflow:hidden;text-overflow:ellipsis" title="${esc(k.source_metric)}">${esc(k.source_metric)}</span>
      <input value="${esc(k.display_name)}" data-f="display_name">
      <input value="${esc(k.category)}" data-f="category">
      <input value="${esc(k.unit)}" data-f="unit">
      <select data-f="direction_good">${dirOpt(k.direction_good)}</select>
      <select data-f="aggregation_type" title="flow=sum · balance=last month · ratio=not aggregated">${aggOpt(k.aggregation_type)}</select>
      <button class="write-only row-del" title="Remove from board" onclick="kpimRemove(${i})">${XICON}</button>
    </div>`).join("")
    :'<div class="empty-state" style="padding:26px">No KPIs selected — add one below.</div>';
  $("kpim-add").innerHTML='<option value="">Add a metric to the board…</option>'
    +kpimState.available.map(m=>`<option>${esc(m)}</option>`).join("");
}
async function kpimRemove(i){
  const k=kpimState.selected[i];
  if(k.id!=null){try{await api(`/kpis/${k.id}`,{method:"DELETE"});}catch(e){toast(e.message);return;}}
  kpimState.selected.splice(i,1);
  if(k.source_metric&&!kpimState.available.includes(k.source_metric))kpimState.available.push(k.source_metric);
  renderKpim();
}
function kpimAdd(){
  const m=$("kpim-add").value;if(!m)return;
  kpimState.available=kpimState.available.filter(x=>x!==m);
  kpimState.selected.push({id:null,source_metric:m,display_name:m,category:"Uncategorized",unit:"USD",direction_good:"up"});
  renderKpim();
}
async function kpimSave(btn){
  // Read current field values out of the DOM into the payload.
  document.querySelectorAll("#kpim-rows .kpi-grid[data-i]").forEach(row=>{
    const k=kpimState.selected[+row.dataset.i];
    row.querySelectorAll("[data-f]").forEach(el=>{k[el.dataset.f]=el.value;});
  });
  btn.disabled=true;btn.textContent="Saving…";
  try{
    await api("/kpis",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({kpis:kpimState.selected.map(k=>({source_metric:k.source_metric,display_name:k.display_name,category:k.category,unit:k.unit,direction_good:k.direction_good,aggregation_type:k.aggregation_type}))})});
    $("kpi-back").classList.remove("open");toast("KPIs updated");await loadFacts();
  }catch(e){toast(e.message);}
  finally{btn.disabled=false;btn.textContent="Save changes";}
}

/* ---------- source doc viewer ---------- */
async function showSourceDoc(chipId){
  // chipId is "ctx_007" (retrieval id) — the numeric part is the context doc id.
  // (id-only: no user strings interpolated into the inline onclick).
  const num=parseInt(String(chipId).replace(/^ctx_/,""),10);
  try{
    const docs=await api("/context");
    const doc=docs.find(d=>d.id===num);
    if(!doc){toast("Source note not found — it may have been deleted");return;}
    $("src-type").textContent=(doc.type||"note").replace(/_/g," ");
    $("src-title").textContent=doc.title;
    $("src-body").textContent=doc.body;
    $("src-meta").textContent=`Effective ${doc.effective_date||"—"}${doc.metric_tags&&doc.metric_tags.length?" · Tags: "+doc.metric_tags.join(", "):""}`;
    $("src-back").classList.add("open");
  }catch(e){toast(e.message||"Could not load source");}
}

/* ---------- category pills ---------- */
let catFilter="", knownCats=[];
function renderCatPills(cats){
  knownCats=cats||knownCats;
  const el=$("cat-pills");if(!el)return;
  if(catFilter&&!knownCats.includes(catFilter))catFilter="";
  const pill=(label,val)=>`<button class="cpill${catFilter===val?" on":""}" data-cat="${esc(val)}">${esc(label)}</button>`;
  const MAX=5;
  let html=pill("All","");
  knownCats.slice(0,MAX).forEach(c=>html+=pill(c,c));
  if(knownCats.length>MAX){
    const rest=knownCats.slice(MAX);
    html+=`<select class="cpill" aria-label="More categories"><option value="">More…</option>${rest.map(c=>`<option${catFilter===c?" selected":""}>${esc(c)}</option>`).join("")}</select>`;
  }
  el.innerHTML=html;
  // listeners (no user strings in inline onclick)
  el.querySelectorAll("button.cpill").forEach(b=>b.onclick=()=>setCat(b.dataset.cat));
  const sel=el.querySelector("select");if(sel)sel.onchange=()=>setCat(sel.value);
}
function setCat(c){catFilter=c||"";renderCatPills();renderCards();}

/* ---------- search / highlight ---------- */
let _searchT=null;
function onSearch(v){clearTimeout(_searchT);_searchT=setTimeout(()=>{searchQuery=v.trim();renderCards();},200);}
function hl(text,q){
  const s=String(text??"");if(!q)return esc(s);
  const i=s.toLowerCase().indexOf(q.toLowerCase());
  if(i<0)return esc(s);
  return esc(s.slice(0,i))+"<mark>"+esc(s.slice(i,i+q.length))+"</mark>"+esc(s.slice(i+q.length));
}
function factMatches(f,q){
  q=q.toLowerCase();
  return (f.metric||"").toLowerCase().includes(q)
    ||(f.category||"").toLowerCase().includes(q)
    ||(f.narrative||"").toLowerCase().includes(q)
    ||(f.sources||[]).some(s=>(s.title||s.id||"").toLowerCase().includes(q));
}

/* Category identity colors — validated 8-slot categorical palette (CVD-safe,
   fixed order, never cycled; >8 categories fall back to neutral). Color always
   appears next to the category NAME, never alone. */
const CAT_PALETTE=["#2a78d6","#1baf7a","#eda100","#008300","#4a3aa7","#e34948","#e87ba4","#eb6834"];
let catSlots={};
function assignCatSlots(cats){catSlots={};cats.forEach((c,i)=>{if(i<CAT_PALETTE.length)catSlots[c]=i;});}
function catColor(c){const i=catSlots[c??"Uncategorized"];return i==null?"":CAT_PALETTE[i];}
function catStyle(c){const col=catColor(c);return col?`--cat:${col}`:"";}

let _toastTimer=null;
function toast(m){const t=$("toast");clearTimeout(_toastTimer);t.classList.remove("has-act");t.textContent=m;t.classList.add("show");_toastTimer=setTimeout(()=>t.classList.remove("show"),2600);}
// A toast with one action button (e.g. Undo). It stays up long enough to reach.
function toastAction(m,label,fn,ms=8000){
  const t=$("toast");clearTimeout(_toastTimer);t.textContent=m;
  const b=document.createElement("button");b.type="button";b.className="toast-act";b.textContent=label;
  b.addEventListener("click",()=>{clearTimeout(_toastTimer);t.classList.remove("show","has-act");fn();});
  t.appendChild(b);t.classList.add("show","has-act");
  _toastTimer=setTimeout(()=>t.classList.remove("show","has-act"),ms);
}
// Map common backend errors to human-friendly copy; falls back to the raw message.
function errMsg(e){
  const m=(e&&e.message)||String(e||"Something went wrong");
  if(/budget/i.test(m))return "Monthly AI budget reached — raise the limit or wait for the next cycle.";
  if(/session expired/i.test(m))return "Your session expired — please sign in again.";
  if(/no active dataset/i.test(m))return "No active dataset — import data first.";
  if(/not enough history/i.test(m))return "Not enough history for this metric yet.";
  return m;
}
let activeWorkspaceId=null;   // v4.0: the tenant scope for every request
async function api(path,opts,_retried){
  opts=opts||{};
  // The session cookie rides along by itself. This header marks the request as
  // ours: the server refuses cookie-signed writes without it (CSRF guard).
  opts.headers={...(opts.headers||{}),"X-Closebrief-Client":"1"};
  if(demoSession)opts.headers["X-Closebrief-Demo"]="1";
  // Tenant scope: the server validates membership, so a spoofed id can't leak data.
  if(activeWorkspaceId!=null)opts.headers={...(opts.headers||{}),"X-Workspace-Id":String(activeWorkspaceId)};
  // The dataset this tab is looking at; the server checks it belongs to the caller.
  if(activeDatasetId!=null)opts.headers={...(opts.headers||{}),"X-Dataset-Id":String(activeDatasetId)};
  const r=await fetch(path,opts);
  if(r.status===401){
    // The session cookie expired (12 hours) or was cleared: sign in again.
    signedIn=false;appStarted=false;
    showLogin();throw new Error("Session expired — please sign in");
  }
  if(!r.ok){
    let data=null;try{data=await r.json();}catch{}
    if(r.status===503&&path==="/generate-insight"&&data&&typeof data.metric==="string"&&
      typeof data.period==="string"&&Number.isFinite(data.value)&&data.narrative===null&&data.faithfulness==="unchecked")return data;
    const err=new Error((data&&data.detail)||r.statusText||"Request failed");err.status=r.status;throw err;
  }
  if(r.status===204)return null; return r.json();
}
// unit "USD" means "an amount of money" (the KPI unit predates currencies);
// pass cur to force one, e.g. AI spend, which is always billed in US dollars.
const fmt=(v,unit,cur)=>{
  if(v===null||v===undefined)return"–";
  if(unit==="%")return(v<0?"−":"")+Math.abs(v).toFixed(1)+"%";
  if(unit==="count"){const n=Math.round(v);return(n<0?"−":"")+Math.abs(n).toLocaleString();}
  // Finance-style: sign BEFORE the currency symbol (−$1.20M / −$370K / −$12,346 /
  // −$12.50), with a real minus sign (U+2212) so it lines up with "+" in mono figures.
  // Whole thousands only from 100K up: "$1K" for $1,450 hid a third of the figure.
  const code=cur||displayCurrency,m=CURRENCY_MARK[code]||"$";
  const a=Math.abs(v),sign=v<0?"−":"";let mag;
  if(a>=1e6)mag=m+(a/1e6).toFixed(2)+"M"; else if(a>=1e5)mag=m+(a/1e3).toFixed(0)+"K";
  else if(a>=1e3)mag=m+Math.round(a).toLocaleString();
  else mag=m+(+a.toFixed(code==="JPY"?0:2)).toLocaleString();
  return sign+mag;
};
// Currency marks, matching app/currency.py. Amounts are shown in the active
// dataset's currency; nothing is converted.
const CURRENCIES=[["USD","$","US dollar"],["EUR","€","Euro"],["GBP","£","British pound"],["INR","₹","Indian rupee"],
  ["JPY","¥","Japanese yen"],["CAD","CA$","Canadian dollar"],["AUD","A$","Australian dollar"],["SGD","S$","Singapore dollar"],["CHF","CHF\u00a0","Swiss franc"]];
const CURRENCY_MARK=Object.fromEntries(CURRENCIES.map(([c,m])=>[c,m]));
let displayCurrency="USD";
const pct=v=>v===null||v===undefined?null:(v>0?"+":v<0?"−":"")+Math.abs(v).toFixed(1)+"%";
// A percent KPI (margin, churn rate) moves in points: 38% against a 40% plan is
// −2.0 pts, not −5.0%. Rankings and the "needs a reason" rule still use the
// relative change; this is only how a variance is written.
const DELTA_KEY={budget:"budget_var_pct",mom:"mom_pct",yoy:"yoy_pct"};
function isPctKpi(f){return !!f&&(f.unit==="%"||f.unit==="percent");}
function deltaPts(f,key){
  const d=f.deltas||{};
  if(key==="budget")return d.budget_var_abs??null;
  if(key==="mom")return f.prior_value!=null&&f.value!=null?f.value-f.prior_value:null;
  const y=d.yoy_pct;   // the year-ago value isn't sent; recover it from the relative change
  return y==null||f.value==null||y===-100?null:f.value-f.value/(1+y/100);
}
function deltaText(f,key){
  const rel=(f.deltas||{})[DELTA_KEY[key]];
  if(rel==null)return null;
  if(!isPctKpi(f))return pct(rel);
  const p=deltaPts(f,key);
  return p==null?null:(p>0?"+":p<0?"−":"")+Math.abs(p).toFixed(1)+" pts";
}
function datasetDisplayName(name){
  const stored=String(name||"");
  if(!/\.(csv|xlsx)$/i.test(stored))return stored;
  const words=stored.replace(/\.(csv|xlsx)$/i,"").replace(/[_-]+/g," ").trim().toLowerCase();
  return words.charAt(0).toUpperCase()+words.slice(1);
}
const capFirst=s=>s.charAt(0).toUpperCase()+s.slice(1);
function priorPeriodLabel(){return granularity==="year"?"vs last year":granularity==="quarter"?"vs last quarter":"vs last month";}
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const plural=(n,one,many)=>`${n} ${n===1?one:(many||one+"s")}`;
const MONTHS=["January","February","March","April","May","June","July","August","September","October","November","December"];
function fmtPeriodTitle(p){
  if(!p)return"Dashboard";
  if(/^\d{4}-\d{2}$/.test(p))return `${MONTHS[+p.slice(5,7)-1]} ${p.slice(0,4)} close`;
  if(/^\d{4}-Q\d$/.test(p))return `Q${p.slice(6)} ${p.slice(0,4)} overview`;
  return `${p} overview`;
}
// People read periods as "Jun 2026"; ISO stays in values, URLs and API calls.
// style: "short" (Jun 2026) | "long" (June 2026) | "axis" (Jun 26).
function fmtPeriod(p,style="short"){
  if(!p)return"—";
  let m=/^(\d{4})-(\d{2})$/.exec(p);
  if(m){const name=MONTHS[+m[2]-1];
    return style==="long"?`${name} ${m[1]}`:style==="axis"?`${name.slice(0,3)} ${m[1].slice(2)}`:`${name.slice(0,3)} ${m[1]}`;}
  m=/^(\d{4})-Q(\d)$/.exec(p);
  if(m)return style==="axis"?`Q${m[2]} ${m[1].slice(2)}`:`Q${m[2]} ${m[1]}`;
  return String(p);
}
// One date format everywhere: "01 Nov 2026". A bare date ("2026-11-01") is a
// calendar day and is shown as written. A timestamp is a moment: the server
// stores UTC, often without a zone ("2026-11-10 20:00:00"), so read it as UTC
// and show the viewer's local date — late-evening UTC is already tomorrow in Asia.
function fmtDate(iso){
  const s=String(iso||""),m=/^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if(!m)return "—";
  const t=/^\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(Z|[+-]\d{2}:?\d{2})?$/.exec(s);
  if(t){
    const d=new Date(`${m[1]}-${m[2]}-${m[3]}T${t[1]}${t[2]||"Z"}`);
    if(!isNaN(d))return `${String(d.getDate()).padStart(2,"0")} ${MONTHS[d.getMonth()].slice(0,3)} ${d.getFullYear()}`;
  }
  return `${m[3]} ${MONTHS[+m[2]-1].slice(0,3)} ${m[1]}`;
}
function setDocTitle(t){document.title=t?`${t} — Closebrief`:"Closebrief — FP&A intelligence";}

/* ---------- reusable modal (replaces native prompt/confirm) ---------- */
let _modalResolve=null;
function closeModal(val){$("modal-back").classList.remove("open");const r=_modalResolve;_modalResolve=null;if(r)r(val===undefined?null:val);}
function _openModalRaw({title,body,okLabel="Save",danger=false,getValue}){
  return new Promise(res=>{
    _modalResolve=res;
    $("modal-title").textContent=title;$("modal-body").innerHTML=body;
    const ok=$("modal-ok");ok.textContent=okLabel;ok.className="btn sm "+(danger?"danger":"primary");
    ok.onclick=()=>{const v=getValue?getValue():true;if(v===undefined)return;closeModal(v);};
    $("modal-back").classList.add("open");
    setTimeout(()=>{const el=$("modal-body").querySelector("textarea,input");if(el){el.focus();if(el.select)el.select();}},40);
  });
}
// Text input/textarea modal → resolves the entered value, or null on cancel.
function modalInput(title,{label="",value="",textarea=false,placeholder="",okLabel="Save",required=false}={}){
  const field=textarea
    ? `<textarea class="modal-field" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`
    : `<input class="modal-field" placeholder="${esc(placeholder)}" value="${esc(value)}">`;
  return _openModalRaw({title,okLabel,
    body:`${label?`<label class="lbl" style="display:block;margin-bottom:6px">${esc(label)}</label>`:""}${field}`,
    getValue:()=>{const el=$("modal-body").querySelector(".modal-field");const v=(el.value||"").trim();return (required&&!v)?undefined:v;}});
}
// Confirm modal → resolves true (confirmed) or null (cancel). `message` is plain text.
function modalConfirm(title,message,{okLabel="Delete",danger=true}={}){
  return _openModalRaw({title,okLabel,danger,
    body:`<div style="font-size:14px;line-height:1.55;color:var(--ink2)">${esc(message)}</div>`,getValue:()=>true});
}

/* tooltip */
const tip=$("tip");
function showTip(h,x,y){tip.innerHTML=h;tip.style.opacity=1;tip.style.left=Math.min(x+14,innerWidth-190)+"px";tip.style.top=(y-10)+"px";}
function hideTip(){tip.style.opacity=0;}

/* ---------- nav ---------- */
const sidebarMedia=window.matchMedia("(max-width: 900px)");
function sidebarFocusable(){
  return [...$("side-nav").querySelectorAll("button,[href],input,select,textarea,[tabindex]:not([tabindex='-1'])")]
    .filter(el=>!el.disabled&&el.getClientRects().length);
}
function syncSidebarA11y(){
  const mobile=sidebarMedia.matches;
  if(!mobile)document.body.classList.remove("sidebar-open");
  const open=mobile&&document.body.classList.contains("sidebar-open");
  const trigger=$("mobile-menu-btn"),side=$("side-nav");
  trigger.setAttribute("aria-expanded",String(open));
  trigger.setAttribute("aria-label",open?"Close menu":"Open menu");
  side.inert=mobile&&!open;
  if(mobile)side.setAttribute("aria-hidden",String(!open));
  else side.removeAttribute("aria-hidden");
}
function toggleSidebar(){
  if(document.body.classList.contains("sidebar-open")){closeSidebar(true);return;}
  document.body.classList.add("sidebar-open");
  syncSidebarA11y();
  (sidebarFocusable()[0]||$("side-nav")).focus();
}
function closeSidebar(restoreFocus=false){
  const mobile=sidebarMedia.matches,side=$("side-nav"),trigger=$("mobile-menu-btn");
  if(mobile&&(restoreFocus||side.contains(document.activeElement)))trigger.focus();
  document.body.classList.remove("sidebar-open");syncSidebarA11y();
}
syncSidebarA11y();
sidebarMedia.addEventListener?.("change",syncSidebarA11y);
function toggleCollapse(){
  const c=document.body.classList.toggle("side-collapsed");
  try{localStorage.setItem("cb-side-collapsed",c?"1":"0");}catch{}
}
// Restore collapsed state on load.
try{if(localStorage.getItem("cb-side-collapsed")==="1")document.body.classList.add("side-collapsed");}catch{}
const VIEWS=["overview","dashboard","digest","context","boardpack","analytics","review","import","costs","notifications","admin","activity"];
// Each close (a month, quarter or year) is a workspace with tabs: [URL key, label, the view that renders it].
const MONTH_TABS=[["overview","Overview","overview"],["kpis","KPIs","dashboard"],["context","Context","context"],
  ["digest","Digest","digest"],["boardpack","Board pack","boardpack"],["data","Data","import"]];
// Places that span closes: [place, view, hash, title].
const PLACES=[["analyze","analytics","#analyze","Analyze"],["library","context","#library","Context library"],
  ["datahub","import","#data","Data and imports"],["costs","costs","#settings=costs","Cost and usage"],
  ["notifications","notifications","#settings=notifications","Notifications"],["admin","admin","#settings=users","Users"],
  ["activity","activity","#settings=activity","Activity"]];
// Hashes from before the month workspace (bookmarks, sent emails) and where they live now.
const LEGACY_HASH={dashboard:{tab:"kpis"},digest:{tab:"digest"},context:{tab:"context"},review:{tab:"overview"},
  import:{tab:"data"},analytics:{place:"analyze"},costs:{place:"costs"},notifications:{place:"notifications"},admin:{place:"admin"}};
// "#month=2026-06&tab=kpis&metric=Net%20revenue" → {tab,period,metric}; "#analyze" → {place}; unknown → null.
function parseRoute(hash){
  const h=(hash||"").replace(/^#/,"");if(!h)return null;
  if(LEGACY_HASH[h])return {...LEGACY_HASH[h]};
  const p=new URLSearchParams(h);
  if(p.has("month")||p.has("tab")){
    const tab=MONTH_TABS.some(t=>t[0]===p.get("tab"))?p.get("tab"):(p.get("metric")?"kpis":"overview");
    return {tab,period:p.get("month")||null,metric:p.get("metric")||null};
  }
  if(p.has("metric"))return {tab:"kpis",period:p.get("period")||null,metric:p.get("metric")};
  if(p.has("settings")){const s=p.get("settings")==="users"?"admin":p.get("settings");
    return PLACES.some(x=>x[0]===s)?{place:s}:null;}
  const place=PLACES.find(x=>x[2]==="#"+h);
  return place?{place:place[0]}:null;
}
function routeHash(r){
  if(r.place)return (PLACES.find(x=>x[0]===r.place)||PLACES[0])[2];
  const q=new URLSearchParams();if(r.period)q.set("month",r.period);q.set("tab",r.tab||"overview");if(r.metric)q.set("metric",r.metric);
  return "#"+q.toString();
}
// Where a person lands with no link: executives and viewers read the digest, everyone else works the close.
function landingTab(role){return role==="executive"||(role==="viewer"&&!demoSession)?"digest":"overview";}
// Every view is linkable: navigation pushes a history entry so Back works,
// and popstate (Back/Forward, a pasted link) routes without pushing again.
function pushRoute(h){if(location.hash!==h)history.pushState(null,"",h);}
let currentPlace={tab:"overview"};
async function route(){
  const r=parseRoute(location.hash)||{tab:landingTab(currentUser&&currentUser.role)};
  if(r.place)return nav(r.place,{fromRoute:true});
  // A link to another close switches the close first, then opens the tab.
  const sel=$("period-select");
  const g=/^\d{4}-\d{2}$/.test(r.period||"")?"month":/^\d{4}-Q[1-4]$/.test(r.period||"")?"quarter":/^\d{4}$/.test(r.period||"")?"year":null;
  if(g&&g!==granularity)await setGranularity(g);
  if(r.period&&sel&&sel.value!==r.period&&[...sel.options].some(o=>o.value===r.period)){sel.value=r.period;await loadFacts();}
  if(r.metric){
    if(currentPeriod)history.replaceState(null,"",metricHash(r.metric,currentPeriod));
    return openMetricFromHash(new URLSearchParams({metric:r.metric,period:currentPeriod||""}).toString());
  }
  nav(r.tab,{fromRoute:true});
  // Normalise legacy or partial links to the canonical URL without a new history entry.
  const canon=routeHash({tab:r.tab,period:currentPeriod});
  if(currentPeriod&&location.hash!==canon)history.replaceState(null,"",canon);
}
// Set once the app has loaded its periods (every boot path: auth, demo, local dev);
// cleared when the login gate shows, so a hash reset on sign-out doesn't route.
let routerReady=false;
window.addEventListener("popstate",()=>importRouteChanged());
window.addEventListener("hashchange",()=>importRouteChanged());
// nav() takes a close tab ("kpis"), a place ("analyze") or an older view name ("dashboard", "import").
function resolveNav(t){
  if(t==="detail")return {tab:"kpis",detail:true};
  if(PLACES.some(p=>p[0]===t))return {place:t};
  if(t==="analytics")return {place:"analyze"};
  if(t==="import"&&!currentPeriod)return {place:"datahub"};
  const tab={dashboard:"kpis",review:"overview",import:"data"}[t]||t;
  return {tab:MONTH_TABS.some(x=>x[0]===tab)?tab:"overview"};
}
const canReview=()=>!!currentUser&&!["viewer","executive"].includes(currentUser.role);
function nav(t,{fromRoute=false}={}){
  closeSidebar();   // dismiss the mobile drawer on navigation
  const r=resolveNav(t);currentPlace=r;
  const v=r.place?PLACES.find(p=>p[0]===r.place)[1]:MONTH_TABS.find(x=>x[0]===r.tab)[2];
  const inMonth=!r.place;
  document.body.classList.toggle("in-month",inMonth);
  $("month-head").style.display=inMonth&&currentPeriod?"":"none";
  const inSettings=["costs","notifications","admin","activity"].includes(r.place);
  document.body.classList.toggle("in-settings",inSettings);
  $("settings-head").style.display=inSettings?"":"none";
  document.querySelectorAll("#settings-head .mtabs a").forEach(a=>{
    if(a.dataset.place===r.place)a.setAttribute("aria-current","page");else a.removeAttribute("aria-current");});
  // Overview carries the review queue beneath the checklist for people who approve.
  for(const s of VIEWS)$("v-"+s).style.display=(s===v||(s==="review"&&v==="overview"&&canReview()&&currentPeriod))?"":"none";
  // The KPIs tab and the open KPI share one workspace: queue + document + inspector.
  $("v-detail").style.display=(r.detail||(v==="dashboard"&&detailFact))?"":"none";
  $("d-empty").style.display=detailFact?"none":"";
  $("ws-split").classList.toggle("has-detail",!!r.detail);
  $("v-dashboard").classList.toggle("detail-open",!!r.detail);
  document.querySelectorAll(".nav-item[data-view]").forEach(item=>{
    const on=item.dataset.view===r.place||(item.dataset.group==="settings"&&inSettings);item.classList.toggle("active",on);
    if(on)item.setAttribute("aria-current","page");else item.removeAttribute("aria-current");
  });
  markCloses();renderMonthHead();
  if(!r.detail){
    if(!fromRoute)pushRoute(routeHash(r.place?r:{tab:r.tab,period:currentPeriod}));
    const tabLabel=MONTH_TABS.find(x=>x[0]===r.tab);
    setDocTitle(r.place?PLACES.find(p=>p[0]===r.place)[3]
      :!currentPeriod?"Start your first close":r.tab==="overview"?fmtPeriodTitle(currentPeriod):`${tabLabel[1]} · ${fmtPeriodTitle(currentPeriod)}`);
  }
  if(v==="overview"){renderOverview();if(canReview()&&currentPeriod)loadReview();}
  if(v==="context"){ctxScope=r.place?"library":"close";loadContext();}
  if(v==="analytics")loadAnalytics();
  if(v==="digest")loadDigest(false);
  if(v==="boardpack")loadBoardPack();
  if(v==="import"){renderDataHealth();const sp=$("sync-panel");if(sp){sp.hidden=r.place!=="datahub";if(r.place==="datahub")loadSyncSources();}}
  if(v==="activity")loadActivity();
  if(v==="costs")loadCosts();
  if(v==="notifications")loadNotifications();
  if(v==="admin")loadAdmin();
}

/* ---------- month workspace: the closes list, the month header, the overview ---------- */
let closeMonths={};       // period → server status from /close/months, when the server has it
let closeShared=new Set(); // periods with a saved digest
let allPeriods=[], showAllCloses=false;
// One close's state from its computed facts. Weights: data in 20%, drafted 30%, approved 40%, shared 10%.
function closeProgress(facts,shared){
  const withData=(facts||[]).filter(f=>f.has_data),n=withData.length;
  const drafted=withData.filter(f=>f.narrative).length,approved=withData.filter(f=>f.review_status==="approved").length;
  const failed=withData.filter(f=>f.faithfulness==="failed").length;
  const exceptions=withData.filter(f=>(f.is_anomaly||f.faithfulness==="failed")&&f.review_status!=="approved").length;
  const progress=n?Math.round((0.2+0.3*drafted/n+0.4*approved/n+(shared?0.1:0))*100)/100:0;
  const status=!n?"empty":approved===n&&shared?"shared":approved===n?"ready":drafted||approved?"in_progress":"not_started";
  return {n,drafted,approved,failed,exceptions,checked:withData.filter(f=>f.faithfulness==="passed").length,shared:!!shared,progress,status};
}
const CLOSE_STATUS={empty:["No data",""],not_started:["Not started",""],in_progress:["In progress","prog"],ready:["Ready to share","prog"],shared:["Shared","done"]};
// The server's summary wins when it has one, so every close in the list is scored the same way.
function closeStateFor(p){
  const m=closeMonths[p];
  if(!m)return p===currentPeriod&&currentFacts?closeProgress(currentFacts,closeShared.has(p)):null;
  return {n:m.kpis,approved:m.approved,drafted:m.drafted,progress:m.progress,status:m.status,shared:m.status==="shared"};
}
function renderCloses(){
  const el=$("close-list");if(!el)return;
  if(!allPeriods.length){el.innerHTML=`<p class="nav-empty">No closes yet. Import a file to start one.</p>`;return;}
  const newest=[...allPeriods].reverse();
  const shown=showAllCloses?newest:newest.slice(0,6);
  if(currentPeriod&&!shown.includes(currentPeriod))shown.push(currentPeriod);
  el.innerHTML=shown.map(p=>{
    const s=closeStateFor(p),[word,cls]=CLOSE_STATUS[s?s.status:"not_started"]||CLOSE_STATUS.not_started;
    const sub=s&&s.n?`${s.approved} of ${s.n}${granularity==="month"?"":" commentaries"} approved`:"";
    return `<button class="ci" data-p="${esc(p)}" data-act="close" data-arg="${esc(p)}">
      <b>${esc(fmtPeriod(p,"long"))}</b>${s?`<span class="cs ${cls}">${word}</span>`:""}
      ${sub?`<small>${sub}</small>`:""}${s&&s.n&&!["not_started","empty"].includes(s.status)?`<span class="cbar" aria-hidden="true"><i style="width:${Math.round(s.progress*100)}%"></i></span>`:""}</button>`;
  }).join("")+(newest.length>6?`<button class="more" onclick="showAllCloses=!showAllCloses;renderCloses()">${showAllCloses?"Show fewer":`Show all ${newest.length}`}</button>`:"");
  markCloses();
}
function markCloses(){
  const inMonth=!currentPlace.place;
  document.querySelectorAll("#close-list .ci").forEach(b=>{
    const on=inMonth&&b.dataset.p===currentPeriod;b.classList.toggle("on",on);
    if(on)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");
  });
}
async function selectClose(p){
  if(!await leaveImport())return;
  const sel=$("period-select");
  const tab=currentPlace.place?"overview":(currentPlace.tab||"overview");
  if(sel.value!==p){sel.value=p;detailFact=null;await loadFacts();}
  nav(tab);
}
async function loadCloseMeta(){
  // Optional server summaries: per-close status (/close/months) and which closes have a digest.
  try{const ms=await api(`/close/months?granularity=${granularity}`);closeMonths=Object.fromEntries(ms.map(m=>[m.period,m]));}catch{closeMonths={};}
  try{const runs=await api("/digests/history");closeShared=new Set(runs.map(r=>r.period));}catch{closeShared=new Set();}
  renderCloses();renderMonthHead();
}
// This close's state. Months are scored from their facts (fresh after every
// approval); a quarter or year has no commentary of its own, so it uses the
// server's roll-up of its months, scored with the same rules.
function currentCloseState(){
  const local=closeProgress(currentFacts,closeShared.has(currentPeriod));
  const m=granularity==="month"?null:closeMonths[currentPeriod];
  if(!m)return local;
  const checked=m.checked??0,shared=m.status==="shared";
  return {n:m.kpis,drafted:m.drafted,approved:m.approved,checked,failed:Math.max(0,m.drafted-checked),
    exceptions:m.exceptions,needs_reason:m.needs_reason,shared,progress:m.progress,
    status:shared?"shared":m.kpis&&m.approved===m.kpis?"ready":m.status};
}
// In a quarter or year, the next step is "open the first month that still needs work".
function rollupNextStep(){
  const st=overviewStatus&&overviewStatus.period===currentPeriod&&(overviewStatus.steps||[]).find(x=>x.state!=="done");
  const m=st&&/^#month=(\d{4}-\d{2})&tab=[a-z]+$/.exec(st.target);
  return m?{label:`Open ${fmtPeriod(m[1],"long")}`,run:`location.hash='${st.target}'`}:null;
}
// The one next step for this close, said as an action; null when nothing is left.
function closeNextStep(s){
  if(!s||!s.n)return {label:"Import data",run:"nav('data')"};
  if(s.drafted<s.n)return {label:`Draft ${plural(s.n-s.drafted,"commentary","commentaries")}`,run:"generateAll()"};
  if(s.failed)return {label:`Check ${plural(s.failed,"flagged KPI","flagged KPIs")}`,run:"nav('kpis')"};
  if(s.approved<s.n)return {label:`Approve ${plural(s.n-s.approved,"draft","drafts")}`,run:"nav('kpis')"};
  if(!s.shared)return {label:"Send the digest",run:"nav('digest')"};
  return null;
}
function renderMonthHead(){
  const head=$("month-head");if(!head)return;
  if(!currentPeriod){head.style.display="none";return;}
  const s=currentCloseState();
  $("mh-title").textContent=fmtPeriodTitle(currentPeriod);
  const [word,cls]=CLOSE_STATUS[(closeStateFor(currentPeriod)||s).status]||CLOSE_STATUS[s.status];
  const pill=$("mh-pill");pill.textContent=word;pill.className="mh-pill "+cls;
  const ds=activeDatasetObj&&datasetDisplayName(activeDatasetObj.name);
  const roll=granularity!=="month"&&closeMonths[currentPeriod];
  $("mh-sub").textContent=(roll
    ?[ds,`${plural(roll.distinct_kpis??s.n,"KPI","KPIs")} across ${plural(roll.month_count||1,"month")}`,s.n?`${s.approved} of ${plural(s.n,"monthly commentary","monthly commentaries")} approved`:null]
    :[ds,s.n?`${plural(s.n,"KPI","KPIs")} computed in code`:null,s.n?`${s.approved} of ${s.n} approved`:null]).filter(Boolean).join(" · ");
  const next=!canReview()?null:granularity==="month"?closeNextStep(s):rollupNextStep(),btn=$("mh-next");
  btn.style.display=next?"":"none";
  if(next){btn.textContent=next.label;btn.setAttribute("onclick",next.run);}
  $("mt-kpis").textContent=s.n||"";
  const tab=currentPlace.place?null:currentPlace.tab;
  document.querySelectorAll("#month-tabs a").forEach(a=>{
    a.href=routeHash({tab:a.dataset.tab,period:currentPeriod});
    if(a.dataset.tab===tab)a.setAttribute("aria-current","page");else a.removeAttribute("aria-current");
  });
}
const BOXTICK='<svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7"/></svg>';
// The close checklist: the server's steps when it has them, otherwise derived from the facts.
function overviewSteps(s,server){
  if(server&&server.steps&&server.steps.length)return server.steps;
  const P=currentPeriod,m=closeMonths[P]||{},flagged=s.drafted-s.checked,undrafted=s.n-s.drafted,remaining=s.n-s.approved,pending=s.drafted-s.approved;
  const needs=m.needs_reason??s.needs_reason??(currentFacts||[]).filter(f=>f.has_data&&(f.is_anomaly||Math.abs(f.deltas?.budget_var_pct||0)>10)&&!f.sources?.length).length;
  const step=(id,title,detail,state,count,tab)=>({id,title,detail,state,count,target:routeHash({tab,period:P})});
  return [
    step("import","Import actuals and budget",s.n?`${plural(s.n,"KPI","KPIs")} imported · open Data to check coverage`:"Import actuals on the Data tab to start this close",s.n?"done":"todo",s.n,"data"),
    step("review","Review the biggest movers",flagged?`${plural(flagged,"draft has","drafts have")} a figure to check · open KPIs`:undrafted?`${plural(undrafted,"KPI is","KPIs are")} not drafted yet · open KPIs`:"Figures checked · open KPIs to review movers",flagged||undrafted?"todo":"done",flagged+undrafted,"kpis"),
    step("reasons",needs?`Add reasons for ${plural(needs,"variance")}`:"Every variance has a reason",needs?`${plural(needs,"variance needs","variances need")} a reason · open Context`:"Reasons recorded · open Context to review notes",needs?"todo":"done",needs,"context"),
    step("approve",pending?`Approve ${plural(pending,"draft")}`:"Approve the drafts",pending?`${plural(pending,"draft needs","drafts need")} approval · open KPIs`:remaining?"Draft commentary on KPIs before approving":"Drafts approved · open KPIs to read commentary",!remaining?"done":pending?"todo":"blocked",pending,"kpis"),
    step("share","Send the digest",s.shared?"Digest sent · open Digest to read it":!remaining?"Open Digest to prepare the leadership summary":"Approve the drafts on KPIs before sharing",s.shared?"done":!remaining?"todo":"blocked",+s.shared,"digest"),
  ];
}
let overviewStatus=null,overviewFor=null;
async function renderOverview(){
  const first=!currentPeriod;
  const grid=document.querySelector("#v-overview .ov-grid");
  if($("ov-welcome"))$("ov-welcome").style.display=first?"":"none";
  if(grid)grid.style.display=first?"none":"";
  if(!$("v-overview")||!currentPeriod){if($("ov-checklist"))$("ov-checklist").innerHTML=currentPeriod?"":`<li class="cl"><span></span><div><b>No closes yet</b><small>Import a CSV or Excel export to start your first close.</small></div><button class="btn primary write-only" onclick="nav('datahub')">Import data</button></li>`;return;}
  const p=currentPeriod;
  paintOverview();
  if(overviewFor!==p){overviewFor=p;overviewStatus=null;
    try{overviewStatus=await api(`/close/status?period=${encodeURIComponent(p)}`);}catch{overviewStatus=null;}
    if(currentPeriod===p){paintOverview();renderMonthHead();}}
}
function paintOverview(){
  const s=currentCloseState();
  $("ov-check-title").textContent=s.status==="shared"?`${fmtPeriodTitle(currentPeriod)} is shared`:"What's left before this close can be shared";
  $("ov-checklist").innerHTML=overviewSteps(s,overviewStatus).map(st=>{
    const done=st.state==="done";
    const act=done?`<span class="st done">Done</span>`:st.state==="blocked"?`<span class="st wait">Waiting</span>`
      :`<a class="btn" href="${esc(st.target)}">${esc(st.id==="import"?"Import":st.id==="share"?"Open digest":"Open")}</a>`;
    return `<li class="cl ${esc(st.state)}"><span class="box">${done?BOXTICK:""}</span><div><b>${esc(st.title)}</b><small>${esc(st.detail||"")}</small></div>${act}</li>`;
  }).join("");
  // At a glance: the four biggest movers against plan (or the prior period without a budget).
  const movers=(currentFacts||[]).filter(f=>f.has_data&&!f.unavailable).map(f=>{
    const plan=f.deltas&&f.deltas.budget_var_pct,v=plan!=null?plan:f.deltas&&f.deltas.mom_pct;return {f,v,plan:plan!=null};
  }).filter(x=>x.v!=null).sort((a,b)=>Math.abs(b.v)-Math.abs(a.v)).slice(0,4);
  $("ov-glance-title").textContent=`${fmtPeriod(currentPeriod,"long")} at a glance`;
  $("ov-glance").innerHTML=movers.length?`<div class="glance">${movers.map(({f,v,plan})=>{
    const good=v===0?null:(f.direction_good==="down"?v<0:v>0);
    return `<button data-act="metric" data-arg="${esc(f.metric)}"><span>${esc(f.metric)}</span><b>${fmt(f.value,f.unit)} <span class="${good===null?"":good?"c-ok":"c-exc"}">${deltaText(f,plan?"budget":"mom")||pct(v)}</span><span class="visually-hidden"> ${plan?"vs plan":"vs prior period"}</span></b></button>`;}).join("")}</div>`
    :`<p class="mh-sub">No movements to show for this close.</p>`;
  if(movers.length&&movers.every(x=>!x.plan)){
    const dataCopy="Add a budget on the Data tab.";
    $("ov-glance").innerHTML+=`<p class="mh-sub">No budget loaded for this close: figures compare with last month. ${canReview()?`<a href="#month=${encodeURIComponent(currentPeriod)}&tab=data">${dataCopy}</a>`:dataCopy}</p>`;
  }
  const act=overviewStatus&&overviewStatus.activity;
  const fallback=[activeDatasetObj&&activeDatasetObj.created_at?{at:activeDatasetObj.created_at,text:`${datasetDisplayName(activeDatasetObj.name)||"Dataset"} imported`}:null,
    s.drafted?{at:null,text:`${s.drafted} of ${s.n} commentaries drafted`}:null,s.approved?{at:null,text:`${s.approved} approved`}:null].filter(Boolean);
  const rows=act&&act.length?act:fallback;
  $("ov-activity").innerHTML=rows.length?rows.map(a=>`<p>${a.at?`<time>${esc(fmtDate(a.at))}</time>`:""}<span>${esc(a.text)}${a.actor?` · ${esc(a.actor)}`:""}</span></p>`).join("")
    :`<p><span>Nothing has happened in this close yet.</span></p>`;
}
function openMetricByName(name){openMetricFromHash(new URLSearchParams({metric:name,period:currentPeriod||""}).toString());}

/* ---------- data tab: what this close's data is missing, as fix items ---------- */
function dataHealth(facts){
  const all=facts||[],withData=all.filter(f=>f.has_data);
  return {total:all.length,withData:withData.length,
    noData:all.filter(f=>!f.has_data).map(f=>f.metric),
    noBudget:withData.filter(f=>!f.aggregated&&(!f.deltas||f.deltas.budget_var_pct==null)).map(f=>f.metric)};
}
function renderDataHealth(){
  if(typeof dropStaleImport==="function")dropStaleImport();
  const el=$("data-health");if(!el)return;
  if(currentPlace.place||!currentPeriod){el.style.display="none";return;}
  const h=dataHealth(currentFacts),P=fmtPeriod(currentPeriod,"long");
  const names=a=>esc(a.slice(0,4).join(", ")+(a.length>4?` and ${a.length-4} more`:""));
  const rows=[
    {done:h.withData>0,title:h.withData?`${plural(h.withData,"KPI","KPIs")} computed for ${P}`:`No figures for ${P} yet`,
      detail:activeDatasetObj?`${esc(datasetDisplayName(activeDatasetObj.name)||"Dataset")}${activeDatasetObj.created_at?` · imported ${esc(fmtDate(activeDatasetObj.created_at))}`:""}`:"",act:""},
    {done:!h.noData.length,title:h.noData.length?`${plural(h.noData.length,"KPI has","KPIs have")} no data for ${P}`:"Every tracked KPI has data",
      detail:h.noData.length?names(h.noData):"",act:h.noData.length?`<button class="write-only btn" onclick="$('imp-mode').value='new';onImportModeChange();$('file-in').click()">Import the missing month</button>`:""},
    {done:!h.noBudget.length,title:h.noBudget.length?`${plural(h.noBudget.length,"KPI has","KPIs have")} no budget`:"Every KPI has a budget to compare against",
      detail:h.noBudget.length?names(h.noBudget)+" · commentary can't say how they did against plan":"",
      act:h.noBudget.length?`<button class="write-only btn" onclick="$('imp-mode').value='join';onImportModeChange();$('drop').scrollIntoView({block:'center'})">Add a budget file</button>`:""},
  ];
  el.style.display="";
  el.innerHTML=`<h2>${esc(P)} data</h2><ol class="checklist">${rows.map(r=>`<li class="cl ${r.done?"done":"todo"}"><span class="box">${r.done?BOXTICK:""}</span>
    <div><b>${r.title}</b>${r.detail?`<small>${r.detail}</small>`:""}</div>${r.done?`<span class="st done">OK</span>`:r.act}</li>`).join("")}</ol>`;
}

/* ---------- board pack tab: the print document, previewed in place ---------- */
let boardPackFor=null;
async function fetchBoardPack(){
  const headers={};
  headers["X-Closebrief-Client"]="1";
  if(demoSession)headers["X-Closebrief-Demo"]="1";
  if(activeWorkspaceId!=null)headers["X-Workspace-Id"]=String(activeWorkspaceId);
  const r=await fetch(`/board-pack?period=${encodeURIComponent(currentPeriod)}`,{headers});
  if(!r.ok){let d=r.statusText;try{d=(await r.json()).detail||d;}catch{}throw new Error(d);}
  return r.text();
}
async function loadBoardPack(){
  const frame=$("bp-frame");if(!frame)return;
  if(!currentPeriod){frame.style.display="none";$("bp-state").innerHTML=screenState("No close to pack yet","Import data to build a board pack.","nav('datahub')","Import data","empty",{writeOnly:true});return;}
  if(boardPackFor===currentPeriod+"|"+activeDatasetId&&frame.srcdoc)return;
  const done=screenLoading("bp-state","Building the board pack…");frame.style.display="none";
  try{
    const html=await fetchBoardPack();if(!done())return;
    frame.srcdoc=html;frame.style.display="";$("bp-state").innerHTML="";boardPackFor=currentPeriod+"|"+activeDatasetId;
  }catch(e){if(done())$("bp-state").innerHTML=screenError(e,"loadBoardPack()");}
}
function printBoardPack(){
  const frame=$("bp-frame");
  if(frame&&frame.srcdoc&&frame.contentWindow){frame.contentWindow.focus();frame.contentWindow.print();}
  else exportBoardPack();
}

/* ---------- auth ---------- */
let appStarted=false;
function initSentry(cfg){
  if(!cfg.sentry_dsn||!window.Sentry)return;
  try{Sentry.init({dsn:cfg.sentry_dsn,environment:cfg.environment||"prod",
    tracesSampleRate:0.1,
    // Don't spam Sentry with expected auth failures.
    ignoreErrors:["Session expired","Missing bearer token"]});}catch(e){}
}
let demoSession=false, guestSession=false, appCfg={};
function showReadOnlyBar(text,exitLabel,signupLabel){
  const b=$("demo-bar");if(!b)return;
  b.style.display="flex";$("demo-bar-text").innerHTML=text;
  $("demo-bar-exit").textContent=exitLabel;$("demo-bar-signup").textContent=signupLabel;
}
function enterDemo(){
  demoSession=true;guestSession=false;signedIn=false;appStarted=false;
  document.body.classList.add("demo");
  showReadOnlyBar("You're exploring the <b>demo</b> — sample data, read-only.","Exit demo","Sign up to use your data");
  hideLogin();startApp();
}
// Guests get the server's read-only viewer role (no token, ALLOW_GUEST=true).
function enterGuest(){
  guestSession=true;demoSession=false;signedIn=false;appStarted=false;
  document.body.classList.add("demo");
  showReadOnlyBar("You're browsing as a <b>guest</b>. Read-only.","Sign in","Create an account");
  hideLogin();startApp();
}
function exitDemo(signup){
  demoSession=false;guestSession=false;document.body.classList.remove("demo");
  const b=$("demo-bar");if(b)b.style.display="none";
  appStarted=false;location.hash="";showLogin();
  $("app-root").style.display="none";
  // "Sign up to use your data" lands the gate in signup mode.
  if(signup&&authMode==="signin")toggleAuthMode();
}
async function boot(){
  let cfg;
  try{cfg=await fetch("/auth/config").then(r=>r.json());}catch{cfg={auth_enabled:false};}
  appCfg=cfg;
  initSentry(cfg);
  if(cfg.demo_enabled){const b=$("lg-demo");if(b)b.style.display="";const n=$("lg-demo-note");if(n)n.style.display="";}
  if(cfg.allow_guest){const g=$("lg-guest");if(g)g.style.display="";const n=$("lg-guest-note");if(n)n.style.display="";}
  // The home page's "Open the demo close" lands on /app?demo=1: go straight in.
  if(cfg.demo_enabled&&new URLSearchParams(location.search).get("demo")==="1"){
    history.replaceState(null,"",location.pathname+location.hash);enterDemo();return;
  }
  // Local-dev bypass. #app-root ships display:none, so reveal it here too.
  if(!cfg.auth_enabled){hideLogin();startApp();return;}
  // Sessions live in an HttpOnly cookie. Older versions kept the token in
  // browser storage: trade it for the cookie once, then delete it.
  const legacy=takeLegacyToken();
  if(legacy)await startSession(legacy).catch(()=>{});
  let status={signed_in:false};
  try{status=await fetch("/auth/session").then(r=>r.json());}catch{}
  if(status.signed_in){signedIn=true;hideLogin();appStarted=true;await startApp();return;}
  if(cfg.google_enabled)initGoogleSignIn(cfg.google_client_id);
  if(!window.supabase||!window.supabase.createClient){
    showLogin();
    // Without Supabase, email/password is unavailable — but Google (if enabled)
    // still works, so only hard-fail when there's no other way in.
    if(!cfg.google_enabled){
      const err=$("lg-err");
      if(err){err.textContent="Auth library failed to load (/vendor/supabase.js). Refresh the page.";err.style.display="block";}
    }
    return;
  }
  // Supabase is only used to sign in. It keeps nothing in browser storage; its
  // token is traded for our session cookie straight away (onSession).
  sb=window.supabase.createClient(cfg.supabase_url,cfg.supabase_anon_key,
    {auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:true}});
  sb.auth.onAuthStateChange((event,s)=>{
    if(s&&s.access_token&&(event==="SIGNED_IN"||event==="INITIAL_SESSION"))onSession(s);
  });
  // An email-confirmation link lands here with a session in the URL.
  const {data:{session}}=await sb.auth.getSession();
  if(session&&session.access_token)onSession(session); else showLogin();
}
/* ================= Google Sign-In (v5.6, optional) =================
   Loaded ON DEMAND: a deployment without a client id never contacts Google. */
const GIS_SRC="https://accounts.google.com/gsi/client";
const SESSION_KEY="cb-session-token";   // where versions before cookie sessions kept it
let _gisPromise=null;           // dedupes the script load across callers
// The credential callback lives in a holder (a "ref"), not captured directly by
// Google's init: a stale closure means the credential arrives and nothing happens.
const _gCallback={fn:null};

// A token left in browser storage by an older version (ours or Supabase's).
// Removed as it's read: from now on only the HttpOnly cookie holds a session.
function takeLegacyToken(){
  let token=null;
  try{
    token=localStorage.getItem(SESSION_KEY);localStorage.removeItem(SESSION_KEY);
    for(const k of Object.keys(localStorage)){
      if(!/^sb-.*-auth-token$/.test(k))continue;
      if(!token){try{const v=JSON.parse(localStorage.getItem(k));token=v.access_token||(v.currentSession&&v.currentSession.access_token)||null;}catch{}}
      localStorage.removeItem(k);
    }
  }catch{}
  return token;
}
// Trade a token for the HttpOnly session cookie; the token isn't kept.
async function startSession(token){
  const r=await fetch("/auth/session",{method:"POST",headers:{Authorization:"Bearer "+token,"X-Closebrief-Client":"1"}});
  if(!r.ok){let d="Sign-in failed";try{d=(await r.json()).detail||d;}catch{}throw new Error(d);}
  signedIn=true;
  return r.json();
}

function loadGisScript(){
  if(_gisPromise)return _gisPromise;                 // one fetch, however many callers
  _gisPromise=new Promise((resolve,reject)=>{
    if(window.google&&window.google.accounts&&window.google.accounts.id)return resolve();
    const s=document.createElement("script");
    s.src=GIS_SRC;s.async=true;s.defer=true;
    s.onload=()=>resolve();
    // Ad blockers and privacy extensions block this host. That must degrade to
    // "no button, use email/password" — never a broken screen.
    s.onerror=()=>reject(new Error("blocked"));
    document.head.appendChild(s);
  });
  return _gisPromise;
}

async function initGoogleSignIn(clientId){
  if(!clientId)return;                                // no client id -> never call Google
  const wrap=$("g-wrap");if(wrap)wrap.style.display="";
  _gCallback.fn=onGoogleCredential;
  try{await loadGisScript();}
  catch{ showGoogleFallback(); return; }
  try{
    google.accounts.id.initialize({
      client_id:clientId,
      // Indirect through the holder so the callback is never stale.
      callback:(resp)=>{ if(_gCallback.fn)_gCallback.fn(resp); },
      cancel_on_tap_outside:true,
    });
    renderGoogleButton();
    window.addEventListener("resize",()=>{
      clearTimeout(window._gRedrawT);
      window._gRedrawT=setTimeout(renderGoogleButton,250);
    });
  }catch{ showGoogleFallback(); }
}

let _gLastWidth=0;
function renderGoogleButton(){
  const slot=$("g-btn");
  if(!slot||!window.google||!google.accounts||!google.accounts.id)return;
  const w=Math.max(200,Math.min(400,Math.round(slot.getBoundingClientRect().width||320)));
  if(w===_gLastWidth&&slot.childElementCount)return;
  _gLastWidth=w;
  try{
    slot.innerHTML="";
    google.accounts.id.renderButton(slot,{
      type:"standard",theme:"filled_black",
      size:"large",text:"signin_with",shape:"rectangular",logo_alignment:"left",width:w,
    });
  }catch{ showGoogleFallback(); }
}

function showGoogleFallback(){
  const slot=$("g-btn"),note=$("g-note");
  if(slot)slot.style.display="none";
  if(note)note.style.display="";
}

async function onGoogleCredential(resp){
  const err=$("lg-err");
  if(!resp||!resp.credential){if(err){err.textContent="Google sign-in returned no credential.";err.style.display="block";}return;}
  if(err)err.style.display="none";
  try{
    const r=await fetch("/auth/google",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({credential:resp.credential})});
    const body=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(body.detail||"Google sign-in failed");
    // The server set our HttpOnly session cookie; nothing is stored here.
    signedIn=true;
    hideLogin();
    if(!appStarted){appStarted=true;await startApp();}
  }catch(e){
    if(err){err.textContent=e.message||"Google sign-in failed.";err.style.display="block";}
  }
}


function showLogin(){routerReady=false;$("login-gate").classList.add("show");$("app-root").style.display="none";}
function hideLogin(){$("login-gate").classList.remove("show");$("app-root").style.display="";}
function toggleAuthMode(){
  authMode=authMode==="signin"?"signup":"signin";
  const up=authMode==="signup";
  $("lg-title").textContent=up?"Create your account":"Sign in";
  $("lg-psub").textContent=up?"The first account becomes the workspace admin.":"Welcome back. Sign in to your workspace.";
  $("lg-btn").textContent=up?"Create account":"Sign in";
  // Password managers offer to generate a password only for new-password.
  $("lg-pass").setAttribute("autocomplete",up?"new-password":"current-password");
  $("lg-toggle").innerHTML=up?'Have an account? <button type="button" onclick="toggleAuthMode()">Sign in</button>'
    :'Need an account? <button type="button" onclick="toggleAuthMode()">Sign up</button>';
  showAuthErrors({});
}
// Field problems, in words that say how to fix them. {} when the form is fine.
function validateAuth(email,pass,mode){
  const errs={};
  if(!email)errs.email="Enter your work email.";
  else if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))errs.email="That email looks incomplete. Check it has an @ and a domain, like you@company.com.";
  if(!pass)errs.pass="Enter your password.";
  else if(mode==="signup"&&pass.length<6)errs.pass="Use at least 6 characters.";
  return errs;
}
// Supabase errors -> what the user can do about them.
function authErrorMessage(e){
  const m=String((e&&e.message)||"");
  if(/invalid login credentials/i.test(m))return "That email and password don't match. Check both and try again.";
  if(/email not confirmed/i.test(m))return "Confirm your email first. Open the link we sent to your inbox, then sign in.";
  if(/already registered|already exists/i.test(m))return "There's already an account for this email. Sign in instead.";
  if(/rate limit|too many/i.test(m))return "Too many attempts. Wait a minute, then try again.";
  if(/network|fetch/i.test(m))return "Couldn't reach the sign-in service. Check your connection and try again.";
  return m||"Sign-in didn't work. Try again.";
}
function showAuthErrors(errs){
  for(const [k,id] of [["email","lg-email"],["pass","lg-pass"]]){
    $(id+"-err").textContent=errs[k]||"";
    if(errs[k])$(id).setAttribute("aria-invalid","true");else $(id).removeAttribute("aria-invalid");
  }
}
function setAuthBusy(busy){
  const b=$("lg-btn");b.disabled=busy;b.setAttribute("aria-busy",String(busy));
  b.textContent=busy?(authMode==="signin"?"Signing in…":"Creating account…"):(authMode==="signin"?"Sign in":"Create account");
}
async function submitAuth(){
  const email=$("lg-email").value.trim(), pass=$("lg-pass").value, err=$("lg-err");
  err.style.display="none";
  if($("lg-btn").disabled)return;   // a second Enter while the first request runs
  const errs=validateAuth(email,pass,authMode);showAuthErrors(errs);
  if(errs.email||errs.pass){$(errs.email?"lg-email":"lg-pass").focus();return;}
  if(!sb){err.textContent="Sign-in isn't available: the auth library didn't load. Refresh the page (Ctrl+Shift+R).";err.style.display="block";return;}
  setAuthBusy(true);
  try{
    const fn=authMode==="signin"?sb.auth.signInWithPassword({email,password:pass}):sb.auth.signUp({email,password:pass});
    const {data,error}=await fn;
    if(error)throw error;
    if(data.session)onSession(data.session);
    else{err.textContent="Check your email to confirm your account, then sign in.";err.style.display="block";setAuthBusy(false);toggleAuthMode();return;}
  }catch(e){err.textContent=authErrorMessage(e);err.style.display="block";}
  setAuthBusy(false);
}
let _sessionFor=null;   // the Supabase token already being traded (sign-in fires twice)
async function onSession(session){
  if(_sessionFor===session.access_token)return;
  _sessionFor=session.access_token;
  try{await startSession(session.access_token);}
  catch(e){const err=$("lg-err");if(err){err.textContent=e.message;err.style.display="block";}showLogin();return;}
  // The Supabase token has done its job; drop it from memory too.
  try{await sb.auth.signOut({scope:"local"});}catch{}
  hideLogin();
  if(appStarted)return;            // don't re-run the full load on token refresh
  appStarted=true;
  await startApp();
}
async function signOut(){
  appStarted=false;
  try{await fetch("/auth/logout",{method:"POST",headers:{"X-Closebrief-Client":"1"}});}catch{}
  takeLegacyToken();                // and anything an older version left behind
  if(window.google&&google.accounts&&google.accounts.id){try{google.accounts.id.disableAutoSelect();}catch{}}
  signedIn=false;
  location.reload();
}
async function startApp(){
  if(signedIn){
    try{currentUser=await api("/me");}catch{currentUser={email:"user",role:"analyst"};}
    applyRole(currentUser);
    try{activeWorkspaceId=+localStorage.getItem("cb-ws")||null;}catch{}
    await loadWorkspaces();   // resolve tenant scope before loading any data
  }else if(demoSession){currentUser={email:"demo@closebrief.app",role:"viewer"};applyRole(currentUser);}
  else if(guestSession){currentUser={email:"Guest",role:"viewer"};applyRole(currentUser);}
  else{applyRole({email:"local@dev",role:"admin"});}
  loadPeriods().then(()=>{routerReady=true;route();});
}
function applyRole(u){
  document.documentElement.setAttribute("data-role",u.role);
  $("user-email").textContent=u.email;
  $("user-avatar").textContent=(u.email||"?").slice(0,2).toUpperCase();
  $("user-role").innerHTML=`<span class="role-badge ${u.role}">${u.role}</span>`;
  $("logout-btn").style.display=signedIn?"":"none";

  const su=$("st-users");if(su)su.style.display=u.role==="admin"?"":"none";
}

/* ---------- admin panel ---------- */
/* ---------- notifications ---------- */
function ntChannelFields(){
  const ch=$("nt-channel").value,lbl=$("nt-target-lbl"),inp=$("nt-target");
  if(ch==="email"){lbl.textContent="Recipients (comma-separated)";inp.placeholder="cfo@company.com, fpa@company.com";}
  else if(ch==="slack"){lbl.textContent="Slack webhook URL";inp.placeholder="https://hooks.slack.com/services/…";}
  else{lbl.textContent="Webhook URL";inp.placeholder="https://your-service.com/hook";}
}
/* ---- Deep link from Slack/email: #metric=NAME&period=P ---- */
function openMetricFromHash(h){
  const p=new URLSearchParams(h);            // "metric=NAME&period=P"
  const name=p.get("metric"),period=p.get("period");
  const go=()=>{
    const f=(currentFacts||[]).find(x=>x.metric===name&&x.has_data)||(currentFacts||[]).find(x=>x.has_data);
    if(!f){nav("dashboard",{fromRoute:true});return;}
    nav("dashboard",{fromRoute:true});const i=shownFacts.indexOf(f);
    if(i>=0)openDetail(i,{fromRoute:true});else{searchQuery="";renderCards();openDetail(shownFacts.indexOf(f),{fromRoute:true});}
  };
  const sel=$("period-select");
  if(period&&sel&&sel.value!==period&&[...sel.options].some(o=>o.value===period)){sel.value=period;loadFacts().then(go);}
  else go();
}

/* ---- Anomaly root-cause drill-down (v5.3) ---- */
async function loadRootCause(){
  if(!detailFact)return;
  const box=$("d-rootcause");box.style.display="";box.innerHTML='<div class="an-empty">Analysing what moved…</div>';
  try{
    const rc=await api(`/insights/root-cause?metric=${encodeURIComponent(detailFact.metric)}&period=${encodeURIComponent(currentPeriod)}`);
    renderRootCause(rc);
  }catch(e){box.innerHTML=`<div class="an-empty">${esc(errMsg(e))}</div>`;}
}
function renderRootCause(rc){
  const box=$("d-rootcause"),unit=rc.unit;
  let z="";
  if(rc.z_score!=null){
    const a=Math.abs(rc.z_score),cls=a>=2?"hi":a>=1?"mid":"lo";
    z=`<span class="rc-z ${cls}">${rc.z_score>0?"+":""}${rc.z_score}σ vs baseline${rc.is_anomaly?" · anomaly":""}</span>`;
  }
  let bars="";
  if(rc.pvm&&rc.pvm.length){
    const max=Math.max(...rc.pvm.map(c=>Math.abs(c.impact)))||1;
    bars=`<div class="rc-sec-label">Price / Volume / Mix of the swing</div>`+rc.pvm.map(c=>{
      const w=Math.round(100*Math.abs(c.impact)/max),cls=c.impact>=0?"pos":"neg";
      return `<div class="rc-bar"><div class="rc-bar-top"><span>${esc(c.component)}</span><span>${fmt(c.impact,unit)} · ${c.share_pct}%</span></div>
        <div class="rc-track"><div class="rc-fill ${cls}" style="width:${w}%"></div></div></div>`;
    }).join("");
  }
  let trend="";
  if(rc.trend)trend=`<div class="rc-drivers" style="margin-top:10px">Trend: <b>${rc.trend.months} consecutive months ${esc(rc.trend.direction)}</b></div>`;
  let drivers="";
  if(rc.drivers&&rc.drivers.length)
    drivers=`<div class="rc-sec-label">Moved with</div><div class="rc-drivers">`+rc.drivers.map(d=>`<b>${esc(d.metric)}</b> (r=${d.r})`).join(" · ")+`</div>`;
  box.innerHTML=`<section class="rc-section">
    <div class="rc-head"><span class="rc-title">Root cause: ${esc(rc.primary_factor||"")}</span>${z}</div>
    ${bars}${trend}${drivers}
    <div style="margin-top:12px"><button class="btn sm write-only" id="d-rc-narr-btn" onclick="rootCauseNarrative()">Explain (AI)</button></div>
    <div id="d-rc-narr" style="margin-top:10px"></div></section>`;
}
async function rootCauseNarrative(){
  if(!detailFact)return;
  const btn=$("d-rc-narr-btn"),t=btn.textContent;btn.disabled=true;btn.textContent="Thinking…";
  try{
    const r=await api("/insights/root-cause/narrative",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({metric:detailFact.metric,period:currentPeriod})});
    $("d-rc-narr").innerHTML=`<div class="narr">${esc(r.narrative)}</div>`;
  }catch(e){toast("Explanation unavailable: "+errMsg(e));}
  finally{btn.disabled=false;btn.textContent=t;}
}

/* ---- Board pack export (v5.1) ---- */
async function exportBoardPack(){
  $("export-menu").classList.remove("open");
  if(!currentPeriod){toast("Import data first");return;}
  const btn=$("export-pack-btn");if(btn)btn.disabled=true;
  try{
    // Fetch with auth headers (a plain new-tab URL wouldn't carry the bearer),
    // then open the returned HTML from a blob so it prints/saves cleanly.
    const headers={};
    headers["X-Closebrief-Client"]="1";
    if(demoSession)headers["X-Closebrief-Demo"]="1";
    if(activeWorkspaceId!=null)headers["X-Workspace-Id"]=String(activeWorkspaceId);
    const r=await fetch(`/board-pack?period=${encodeURIComponent(currentPeriod)}`,{headers});
    if(!r.ok){let d=r.statusText;try{d=(await r.json()).detail||d;}catch{}throw new Error(d);}
    const url=URL.createObjectURL(new Blob([await r.text()],{type:"text/html"}));
    const win=window.open(url,"_blank");
    if(!win)toast("Allow pop-ups to open the board pack");
    setTimeout(()=>URL.revokeObjectURL(url),60000);
  }catch(e){toast("Export failed: "+errMsg(e));}
  finally{if(btn)btn.disabled=false;}
}

/* ---------- secondary screen states ---------- */
const screenLoads=new Map();
// Pass {writeOnly:true} for actions that change data, so read-only people never see them.
function screenState(title,body,action,label,kind="empty",{writeOnly=false}={}){
  return `<div class="screen-state ${esc(kind)}" role="${kind==="error"?"alert":"status"}"><h2>${esc(title)}</h2><p>${esc(body)}</p>${action?`<button class="btn primary${writeOnly?" write-only":""}" onclick="${esc(action)}">${esc(label)}</button>`:""}</div>`;
}
function screenRow(html,cols){return cols?`<tr><td colspan="${esc(cols)}">${html}</td></tr>`:html;}
function screenLoading(id,message,cols=0){
  const el=$(id),previous=screenLoads.get(id);if(previous)clearTimeout(previous.timer);
  const state={};screenLoads.set(id,state);
  el.innerHTML=screenRow(`<div class="screen-state" role="status"><div class="sk sk-line" aria-hidden="true"></div><p>${esc(message)}</p><p class="screen-wait" hidden>Still working — this is taking longer than usual.</p></div>`,cols);
  el.setAttribute("aria-busy","true");
  state.timer=setTimeout(()=>{if(screenLoads.get(id)===state){const line=el.querySelector(".screen-wait");if(line)line.hidden=false;}},4000);
  return ()=>{if(screenLoads.get(id)!==state)return false;clearTimeout(state.timer);el.removeAttribute("aria-busy");return true;};
}
function screenError(e,retry){
  const m=(e&&e.message)||"";
  const reason=/403|forbidden|admin|not allowed|insufficient.*role/i.test(m)?"You need admin access to see this."
    :/401|session expired|unauthori[sz]ed/i.test(m)?"Your session expired. Sign in again, then retry."
    :/fetch|network|offline|connection/i.test(m)?"We couldn't reach Closebrief. Check your connection and try again."
    :/no active dataset/i.test(m)?"There is no active dataset. Import data from the dashboard, then retry."
    :/budget/i.test(m)?"The monthly AI budget has been reached. Ask an admin to raise the limit or wait for the next cycle."
    :"We couldn't load this information. Please try again.";
  return screenState("Couldn't finish loading",reason,retry,"Retry","error");
}

/* ================= ADVANCED ANALYTICS (v5.0) ================= */
let _anMetrics=[], _lastForecast=null, _scnT=null;
function _metricList(){return (currentFacts||[]).filter(f=>f.has_data).slice().sort((a,b)=>Math.abs(b.value??0)-Math.abs(a.value??0)).map(f=>({name:f.metric,unit:f.unit}));}
function _fillMetricSelect(id){
  const sel=$(id);if(!sel)return;const prev=sel.value;
  sel.innerHTML=_anMetrics.length?_anMetrics.map(m=>`<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("")
    :'<option value="">No metrics — import data first</option>';
  if(prev&&_anMetrics.some(m=>m.name===prev))sel.value=prev;
}
// Analyze shows one tool at a time; the list on the left picks it.
let anToolIdx=0;
function anTool(i){
  anToolIdx=i;
  document.querySelectorAll(".an-tools button").forEach(b=>b.setAttribute("aria-pressed",String(+b.dataset.tool===i)));
  document.querySelectorAll(".an-one>.panel").forEach((p,k)=>p.style.display=k===i?"":"none");
  window.dispatchEvent(new Event("resize"));   // charts in a panel that was hidden size themselves now
}
async function loadAnalytics(){
  anTool(anToolIdx);
  const done=screenLoading("analytics-state","Loading advanced analytics…");$("analytics-tools").style.display="none";
  try{
    // On a deep link, facts may not have arrived from the dashboard yet.
    if(!(currentFacts&&currentFacts.length)&&currentPeriod)currentFacts=await api(`/facts?period=${encodeURIComponent(currentPeriod)}&granularity=${granularity}`);
    if(!done())return;
    _anMetrics=_metricList();_fillMetricSelect("fc-metric");_fillMetricSelect("scn-metric");
    if(!_anMetrics.length){$("analytics-state").innerHTML=currentFacts.length
      ?screenState("No metrics for "+fmtPeriod(currentPeriod),"Choose a period with actual values on the dashboard to forecast results and explore scenarios.","nav('dashboard')","Open the dashboard")
      :screenState("No metrics to analyse yet","Import metrics to forecast results, explore scenarios and compare datasets.","nav('import')","Import data","empty",{writeOnly:true});return;}
    $("analytics-state").innerHTML="";$("analytics-tools").style.display="";
    $("kb-chips").innerHTML=_anMetrics.map((m,i)=>`<span class="fchip" role="button" tabindex="0" onclick="insertMetricToken(${esc(i)})">${esc(m.name)}</span>`).join("");
    onLever();$("scn-out").innerHTML='<div class="an-empty">Pick a metric and move a lever to project.</div>';
    await loadCrossDomain();
  }catch(e){if(done())$("analytics-state").innerHTML=screenError(e,"loadAnalytics()");}
}
/* ---- Forecast ---- */
async function runForecast(){
  const metric=$("fc-metric").value;if(!metric){toast("Pick a metric first");return;}
  const horizon=$("fc-horizon").value||3;
  $("fc-meta").innerHTML="";$("fc-narr").innerHTML="";$("fc-narr-btn").style.display="none";
  const box=$("box-c-forecast");box.innerHTML='<div class="loading">Projecting…</div>';
  try{
    const fc=await api(`/forecast?metric=${encodeURIComponent(metric)}&horizon=${horizon}`);
    _lastForecast=fc;renderForecastChart(fc);
    const thin=(fc.n_history||0)<8;               // too few points for a trustworthy backtest
    const mape=fc.mape==null?"–":(+fc.mape.toFixed(1))+"%";
    const next=fc.projections&&fc.projections[0]?fc.projections[0].value:null;
    $("fc-meta").innerHTML=
      `<div class="stat"><div class="stat-lbl">Next period</div><div class="card-value">${fmt(next,fc.unit)}</div></div>`+
      `<div class="stat" style="--sc:var(--mut)"><div class="stat-lbl" title="Average error of this method on past months">Typical error</div><div class="card-value">${mape}</div>`+
      `${thin?'<div class="psub" style="margin-top:2px">Only '+(fc.n_history||0)+' periods of history, so treat this as a rough guide</div>':""}</div>`;
    $("fc-narr-btn").style.display="";
  }catch(e){if(chartReg["c-forecast"]){chartReg["c-forecast"].destroy();delete chartReg["c-forecast"];}box.innerHTML=`<div class="an-empty">${esc(errMsg(e))}</div>`;}
}
function renderForecastChart(fc){
  ensureCanvas("c-forecast");const box=$("box-c-forecast");
  const hist=fc.history||[],proj=fc.projections||[];
  if(!hist.length&&!proj.length){if(chartReg["c-forecast"]){chartReg["c-forecast"].destroy();delete chartReg["c-forecast"];}box.innerHTML='<div class="an-empty">Not enough history to forecast.</div>';return;}
  const th=chartTheme();
  const labels=[...hist.map(h=>h.period),...proj.map(p=>p.period)].map(p=>String(p).slice(2));
  const actual=[...hist.map(h=>h.value),...proj.map(()=>null)];
  const forecast=[...hist.map(()=>null),...proj.map(p=>p.value)];
  if(hist.length&&proj.length)forecast[hist.length-1]=hist[hist.length-1].value;   // connect the dashed line to the last actual
  mkChart("c-forecast",{type:"line",
    data:{labels,datasets:[
      {label:"Actual",data:actual,borderColor:th.accent,borderWidth:2,tension:.3,pointRadius:0,pointHoverRadius:4,spanGaps:false,
        fill:true,backgroundColor:ctx=>{const{chartArea,ctx:c}=ctx.chart;if(!chartArea)return"transparent";
          const g=c.createLinearGradient(0,chartArea.top,0,chartArea.bottom);g.addColorStop(0,th.accent+"33");g.addColorStop(1,th.accent+"03");return g;}},
      {label:"Forecast",data:forecast,borderColor:th.accent,borderWidth:2,borderDash:[5,4],tension:.3,pointRadius:0,pointHoverRadius:4,spanGaps:true}]},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      plugins:{legend:{display:false},tooltip:tooltipCfg(fc.unit)},
      scales:{y:{grid:{color:th.grid},ticks:{color:th.mut,maxTicksLimit:5,callback:v=>fmt(v,fc.unit)},border:{display:false}},
        x:{grid:{display:false},ticks:{color:th.mut,maxRotation:0,autoSkip:true,maxTicksLimit:8},border:{color:th.grid}}}}});
}
async function forecastNarrative(){
  const metric=$("fc-metric").value;if(!metric)return;
  const horizon=+($("fc-horizon").value||3),btn=$("fc-narr-btn"),t=btn.textContent;
  btn.disabled=true;btn.textContent="Thinking…";
  try{
    const r=await api("/forecast/narrative",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({metric,horizon})});
    $("fc-narr").innerHTML=`<div class="narr">${esc(r.narrative)}</div>`;
  }catch(e){toast("Narrative unavailable: "+errMsg(e));}
  finally{btn.disabled=false;btn.textContent=t;}
}
/* ---- Scenario (what-if) ---- */
function onLever(){
  for(const k of["price","volume","mix"]){
    const el=$("scn-"+k);if(!el)continue;const v=+el.value,out=$("scn-"+k+"-v");
    out.textContent=(v>0?"+":"")+v+"%";out.className="lever-val"+(v>0?" pos":v<0?" neg":"");
  }
}
function runScenario(){
  const metric=$("scn-metric").value;if(!metric)return;
  clearTimeout(_scnT);
  _scnT=setTimeout(async()=>{
    const body={metric,price_pct:+$("scn-price").value,volume_pct:+$("scn-volume").value,mix_pct:+$("scn-mix").value};
    try{
      const r=await api("/scenario",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const unit=(_anMetrics.find(m=>m.name===metric)||{}).unit;
      const cls=r.impact_abs>0?"pos":r.impact_abs<0?"neg":"",sign=r.impact_abs>0?"+":"";
      $("scn-out").innerHTML=
        `<div class="scn-cell"><span class="k">Base</span><span class="v">${fmt(r.base_value,unit)}</span></div>`+
        `<div class="scn-cell"><span class="k">Projected</span><span class="v ${cls}">${fmt(r.projected_value,unit)}</span></div>`+
        `<div class="scn-cell"><span class="k">Impact</span><span class="v ${cls}">${sign}${fmt(r.impact_abs,unit)}${r.impact_pct!=null?` · ${sign}${(+r.impact_pct.toFixed(1))}%`:""}</span></div>`+
        (r.vs_budget!=null?`<div class="scn-cell"><span class="k">vs Budget</span><span class="v ${r.vs_budget>=0?"pos":"neg"}">${fmt(r.vs_budget,unit)}</span></div>`:"");
    }catch(e){$("scn-out").innerHTML=`<div class="an-empty">${esc(errMsg(e))}</div>`;}
  },120);
}
/* ---- Custom KPI (formula builder) ---- */
function insertMetricToken(i){
  const m=_anMetrics[i];if(!m)return;const inp=$("kb-formula");
  const sep=(inp.value&&!/[\s([+\-*/]$/.test(inp.value))?" ":"";
  inp.value+=sep+"["+m.name+"]";inp.focus();
}
async function createDerivedKpi(){
  const name=$("kb-name").value.trim(),formula=$("kb-formula").value.trim();
  if(!name||!formula){toast("Name and formula are required");return;}
  const btn=$("kb-btn");btn.disabled=true;
  try{
    await api("/kpis/derived",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name,formula,unit:$("kb-unit").value,category:$("kb-cat").value.trim()||"Derived",direction_good:$("kb-dir").value})});
    toast("Created “"+name+"”");
    $("kb-name").value="";$("kb-formula").value="";
    await loadFacts();                 // recompute pulls the new derived metric onto the board
    _anMetrics=_metricList();_fillMetricSelect("fc-metric");_fillMetricSelect("scn-metric");
    const chips=$("kb-chips");if(chips)chips.innerHTML=_anMetrics.map((m,i)=>`<span class="fchip" role="button" tabindex="0" onclick="insertMetricToken(${i})">${esc(m.name)}</span>`).join("");
  }catch(e){toast("Failed: "+errMsg(e));}
  finally{btn.disabled=false;}
}
/* ---- Cross-dataset signals ---- */
async function loadCrossDomain(){
  const box=$("xd-list");if(!box)return;const done=screenLoading("xd-list","Looking for links between datasets…");
  try{
    const rows=await api("/insights/cross-domain");if(!done())return;
    if(!rows.length){box.innerHTML=screenState("No links between datasets yet","Import a second dataset that covers the same months to see which metrics move together.","nav('import')","Import another dataset","empty",{writeOnly:true});return;}
    box.innerHTML=rows.slice(0,12).map(p=>{
      const cls=p.direction==="positive"?"pos":"neg";
      const lag=p.lag?`${Math.abs(p.lag)}-period ${p.lag>0?"lead":"lag"}`:"same period";
      return `<div class="xd-item"><div class="xd-r ${cls}">${p.r>0?"+":""}${p.r}</div>
        <div class="xd-body"><div class="xd-pair">${esc(p.metric_a)} <span class="xd-arrow">↔</span> ${esc(p.metric_b)}</div>
        <div class="xd-meta">${esc(datasetDisplayName(p.dataset_a))} → ${esc(datasetDisplayName(p.dataset_b))} · ${lag} · ${p.months} mo overlap</div></div></div>`;
    }).join("");
  }catch(e){if(done())box.innerHTML=screenError(e,"loadCrossDomain()");}
}
/* ---- Collaborative review (detail view) ---- */
// Approval is a separate check from figure verification — see docs/ui-standard.md §3.
const REVIEW_LABELS={none:"Not approved yet",pending:"Awaiting approval",approved:"Approved",changes_requested:"Changes requested"};
// Figures check and explanation confidence, as two clearly separate statements.
function narrMeta(f){
  const bits=[];
  if(f.faithfulness==="passed")bits.push("Figures checked");
  else if(f.faithfulness==="unchecked"&&f.narrative)bits.push("Figures changed since this was written");
  else if(f.faithfulness==="failed")bits.push("Figures need review");
  if(f.confidence)bits.push(`Explanation: ${f.confidence} confidence`);
  if(f.edited)bits.push("edited by analyst");
  if(f.cost_usd!=null)bits.push(`≈ $${(+f.cost_usd).toFixed(4)}`);
  return bits.join(" · ");
}
function renderReviewStrip(f){
  const readonly=$("d-readonly-approval");
  if(readonly){
    readonly.style.display=canReview()?"none":"";
    readonly.innerHTML=canReview()?"":`<button class="btn sm" disabled>Approve</button><p>${demoSession?`<a href="#signup" onclick="event.preventDefault();exitDemo(true)">Sign up to approve commentary</a>`:"Only analysts and admins can approve"}</p>`;
  }
  const strip=$("d-rev-strip");if(!strip)return;
  if(!f||!f.report_id){strip.style.display="none";$("d-versions").style.display="none";$("d-assign-row").style.display="none";return;}
  strip.style.display="";
  const st=f.review_status||"none",badge=$("d-rev-badge");
  badge.className="rev-badge "+st;
  badge.textContent=REVIEW_LABELS[st]||REVIEW_LABELS.none;
  $("d-assigned").textContent=f.assigned_email?("· "+f.assigned_email):"";
  $("d-assign-row").style.display="none";
  const vb=$("d-versions");vb.style.display="none";vb.innerHTML="";$("d-ver-btn").textContent="History";
}
async function reviewReport(status){
  if(!detailFact||!detailFact.report_id)return;
  try{
    await api(`/reports/${detailFact.report_id}/review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({status})});
    detailFact.review_status=status;renderReviewStrip(detailFact);
    const cf=(currentFacts||[]).find(x=>x.report_id===detailFact.report_id);if(cf)cf.review_status=status;
    toast(status==="approved"?"Commentary approved":"Changes requested");
    refreshReviewBadge();
  }catch(e){toast("Failed: "+errMsg(e));}
}
/* ---- Reviewer assignment + review queue (v5.2) ---- */
async function loadWsMembers(){
  const dl=$("ws-members");if(!dl)return;
  if(activeWorkspaceId==null){dl.innerHTML="";return;}
  try{
    const ms=await api(`/workspaces/${activeWorkspaceId}/members`);
    dl.innerHTML=(ms||[]).filter(m=>m.email).map(m=>`<option value="${esc(m.email)}">${esc(m.role||"")}</option>`).join("");
  }catch{dl.innerHTML="";}
}
function toggleAssign(){
  const row=$("d-assign-row"),show=row.style.display==="none";
  row.style.display=show?"flex":"none";
  if(show){loadWsMembers();$("d-assign-email").focus();}
}
async function assignReviewer(){
  if(!detailFact||!detailFact.report_id)return;
  const email=$("d-assign-email").value.trim();
  if(!email){toast("Enter a reviewer email");return;}
  try{
    await api(`/reports/${detailFact.report_id}/assign`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email})});
    detailFact.review_status="pending";detailFact.assigned_email=email;
    const cf=(currentFacts||[]).find(x=>x.report_id===detailFact.report_id);if(cf){cf.review_status="pending";cf.assigned_email=email;}
    renderReviewStrip(detailFact);$("d-assign-email").value="";
    toast("Assigned to "+email+" — they’ve been emailed");
    refreshReviewBadge();
  }catch(e){toast("Assign failed: "+errMsg(e));}
}
let _reviewItems=[];
async function loadReview(){
  const box=$("rq-list");if(!box)return;const done=screenLoading("rq-list","Loading the review queue…");
  let items=[];
  try{items=await api("/reports/review-queue?all_datasets=true");}
  catch(e){if(done())box.innerHTML=screenError(e,"loadReview()");return;}
  if(!done())return;
  _reviewItems=items;updateReviewBadge(items.length);
  if(!box)return;
  if(!items.length){box.innerHTML=screenState("Nothing assigned to you","Commentary someone assigns to you for approval appears here. Drafts for this close are on the KPIs tab.","nav('kpis')","Open KPIs");return;}
  box.innerHTML=items.map((it,i)=>`<div class="rq-item" role="button" tabindex="0" onclick="openReviewItem(${esc(i)})">
    <div class="rq-main"><div class="rq-metric">${esc(it.metric)}</div>
    <div class="rq-preview">${esc(it.preview||"No preview")}</div>
    <div class="rq-meta">${esc(datasetDisplayName(it.dataset_name))}</div></div>
    <span class="rq-period">${esc(fmtPeriod(it.period))}</span>
    <svg class="rq-open" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg></div>`).join("");
}
async function openReviewItem(i){
  const it=_reviewItems[i];if(!it)return;
  // Switch to the item's dataset first when it isn't the active one.
  if(it.dataset_id!=null&&it.dataset_id!==activeDatasetId){
    try{await api(`/datasets/${it.dataset_id}/activate`,{method:"POST"});await loadPeriods();}
    catch(e){toast("Couldn't open: "+errMsg(e));return;}
  }
  const sel=$("period-select");
  if(sel&&it.period&&sel.value!==it.period&&[...sel.options].some(o=>o.value===it.period)){sel.value=it.period;await loadFacts();}
  nav("dashboard");
  const f=(currentFacts||[]).find(x=>x.metric===it.metric&&x.has_data);
  if(!f){toast("Couldn’t open this metric on the current board");return;}
  const idx=shownFacts.indexOf(f);
  if(idx>=0)openDetail(idx);else{searchQuery="";renderCards();openDetail(shownFacts.indexOf(f));}
}
function updateReviewBadge(n){
  const b=$("review-badge");if(!b)return;
  if(n>0){b.textContent=n;b.style.display="";}else b.style.display="none";
}
async function refreshReviewBadge(){
  try{updateReviewBadge((await api("/reports/review-queue?all_datasets=true")).length);}catch{}
}
async function toggleVersions(){
  const box=$("d-versions");
  if(box.style.display!=="none"){box.style.display="none";$("d-ver-btn").textContent="History";return;}
  if(!detailFact||!detailFact.report_id)return;
  box.style.display="";$("d-ver-btn").textContent="Hide history";box.innerHTML='<div class="an-empty">Loading…</div>';
  try{
    const vers=await api(`/reports/${detailFact.report_id}/versions`);
    if(!vers.length){box.innerHTML='<div class="an-empty">No prior versions — edits to the narrative create new versions.</div>';return;}
    box.innerHTML=vers.map(v=>{
      // diff is an array of unified-diff lines (never a string).
      const lines=Array.isArray(v.diff)?v.diff:(v.diff?String(v.diff).split("\n"):[]);
      const diff=lines.length?`<div class="ver-diff">${lines.map(l=>{const e=esc(l);
        return /^\+/.test(l)&&!/^\+\+\+/.test(l)?`<span class="add">${e}</span>`:/^-/.test(l)&&!/^---/.test(l)?`<span class="del">${e}</span>`:e;}).join("\n")}</div>`:"";
      return `<div class="ver-item"><div class="ver-head">v${v.version}${v.editor_email?` · ${esc(v.editor_email)}`:""}<span class="when">${esc(v.created_at||"")}</span></div>${diff}</div>`;
    }).join("");
  }catch(e){box.innerHTML=`<div class="an-empty">${esc(errMsg(e))}</div>`;}
}

async function loadNotifications(){
  const done=screenLoading("nt-list","Loading notification channels…");const schedules=loadSchedules();
  try{
    const cfgs=await api("/notifications/configs");if(!done())return;
    const icon={email:ICON_PATHS.mail,slack:ICON_PATHS.chat,webhook:ICON_PATHS.link};
    $("nt-list").innerHTML=cfgs.length?cfgs.map(c=>{
      const cf=c.config||{},target=cf.recipients?cf.recipients.join(", "):(cf.webhook_url||cf.url||"—");
      const events=(cf.events||["all"]).join(", ");
      return`<div class="card" style="margin-top:12px;flex-direction:row;align-items:center;gap:14px">
        <span class="nt-ic"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${icon[c.channel]||ICON_PATHS.bell}</svg></span>
        <div style="flex:1;min-width:0">
          <b style="text-transform:capitalize">${esc(c.channel)}</b>
          <div style="font-size:12px;color:var(--mut);word-break:break-all">${esc(target)}</div>
          <div style="font-size:12px;color:var(--mut2);margin-top:2px">Events: ${esc(events)}</div>
        </div>
        <label style="font-size:12px;color:var(--ink3);font-weight:600;display:flex;align-items:center;gap:6px">
          <input type="checkbox" ${c.enabled?"checked":""} onchange="ntToggle(${esc(c.id)},this.checked)"> Enabled</label>
        <button class="write-only btn sm" onclick="ntTest(${esc(c.id)},this)">Send test</button>
        <button class="write-only ico" title="Delete channel" aria-label="Delete channel" onclick="ntDelete(${esc(c.id)})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px">${ICON_PATHS.trash}</svg></button>
      </div>`;
    }).join(""):screenState("No notification channels yet","Send digests and anomaly alerts to email, Slack or a webhook.","$('nt-target').focus()","Add a channel","empty",{writeOnly:true});
  }catch(e){if(done())$("nt-list").innerHTML=screenError(e,"loadNotifications()");}
  await schedules;
}
/* ---------- schedules (Phase 2+4) ---------- */
function schKindFields(){const k=$("sch-kind").value;$("sch-topn-wrap").style.display=k==="digest"?"":"none";$("sch-recip-wrap").style.display=k==="board_pack"?"":"none";}
const CADENCE_LABEL={daily:"Daily",weekly:"Weekly",monthly:"Monthly"};
async function loadSchedules(){
  const done=screenLoading("sch-list","Loading schedules…");
  let jobs;try{jobs=await api("/schedules");}catch(e){if(done())$("sch-list").innerHTML=screenError(e,"loadSchedules()");return;}
  if(!done())return;
  const kindLabel={digest:"Scheduled digest",anomaly_scan:"Anomaly scan",board_pack:"Board pack email"};
  $("sch-list").innerHTML=jobs.length?jobs.map(j=>`
    <div class="card" style="margin-top:12px;flex-direction:row;align-items:center;gap:14px">
      <span class="nt-ic"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${j.kind==="digest"?'<path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h8M8 17h5"/>':'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>'}</svg></span>
      <div style="flex:1;min-width:0">
        <b>${esc(kindLabel[j.kind]||j.kind)}</b>
        <div style="font-size:12px;color:var(--mut)">${esc(CADENCE_LABEL[j.cadence]||j.cadence)}${j.kind==="digest"&&j.config&&j.config.top_n?` · top ${esc(j.config.top_n)}`:""}${j.next_run_at?` · next ${esc(fmtDate(j.next_run_at))}`:""}</div>
        ${j.last_status?`<div style="font-size:12px;margin-top:2px;color:${j.last_status==="error"?"var(--red)":j.last_status==="ok"?"var(--green)":"var(--mut2)"}">last run: ${esc(j.last_status)}${j.fail_count?` · ${esc(j.fail_count)} consecutive failures`:""}${j.last_status==="error"&&j.last_error?` — ${esc("The scheduled run failed. Check the channel and job configuration before the next run.")}`:""}</div>`:""}
      </div>
      <label style="font-size:12px;color:var(--ink3);font-weight:600;display:flex;align-items:center;gap:6px">
        <input type="checkbox" ${j.enabled?"checked":""} onchange="schToggle(${esc(j.id)},this.checked)"> Enabled</label>
      <button class="write-only ico" title="Delete schedule" aria-label="Delete schedule" onclick="schDelete(${esc(j.id)})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px">${ICON_PATHS.trash}</svg></button>
    </div>`).join(""):screenState("No schedules yet","Schedule digests, anomaly scans or board pack emails to run automatically.","$('sch-kind').focus()","Add a schedule","empty",{writeOnly:true});
}
async function schCreate(){
  const kind=$("sch-kind").value,cadence=$("sch-cadence").value;
  const body={kind,cadence};
  if(kind==="digest")body.top_n=Math.max(1,Math.min(20,parseInt($("sch-topn").value)||5));
  if(kind==="board_pack"){
    body.recipients=($("sch-recip").value||"").split(",").map(s=>s.trim()).filter(Boolean);
    if(!body.recipients.length){toast("Add at least one recipient email");return;}
  }
  try{await api("/schedules",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    toast("Schedule added");loadSchedules();
    let seen=false;try{seen=localStorage.getItem("cb-sched-hint")==="1";}catch{}
    if(!seen){try{localStorage.setItem("cb-sched-hint","1");}catch{}
    _openModalRaw({title:"One more step to activate scheduling",okLabel:"Got it",
      body:`<div style="font-size:14px;line-height:1.6;color:var(--ink2)">
        <p style="margin-bottom:10px">Schedules run when an external cron calls Closebrief. Render's free tier has no built-in cron, so:</p>
        <ol style="margin:0 0 4px 18px;display:flex;flex-direction:column;gap:7px">
          <li>Set a <b>SCHEDULER_TOKEN</b> env var in Render (any long random secret).</li>
          <li>At <b>cron-job.org</b> (free), add a job that POSTs to <code>${esc(location.origin)}/internal/scheduler/tick</code> with header <code>X-Scheduler-Token: &lt;your token&gt;</code>, every hour.</li>
        </ol></div>`,getValue:()=>true});}
  }catch(e){toast(e.message);}
}
async function schToggle(id,enabled){try{await api(`/schedules/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled})});}catch(e){toast(e.message);loadSchedules();}}
async function schDelete(id){if(!await modalConfirm("Delete schedule","Remove this scheduled job?",{okLabel:"Delete"}))return;try{await api(`/schedules/${id}`,{method:"DELETE"});toast("Deleted");loadSchedules();}catch(e){toast(e.message);}}
async function ntCreate(){
  const channel=$("nt-channel").value,raw=$("nt-target").value.trim();
  if(!raw){toast("Enter a target (recipients or URL)");return;}
  const events=$("nt-events").value.split(",");
  let config={events};
  if(channel==="email")config.recipients=raw.split(",").map(s=>s.trim()).filter(Boolean);
  else if(channel==="slack")config.webhook_url=raw;
  else config.url=raw;
  try{await api("/notifications/configs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({channel,config})});
    $("nt-target").value="";toast("Channel added");loadNotifications();
  }catch(e){toast(e.message);}
}
async function ntToggle(id,enabled){try{await api(`/notifications/${id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled})});}catch(e){toast(e.message);}}
async function ntDelete(id){if(!await modalConfirm("Delete channel","Remove this notification channel?",{okLabel:"Delete"}))return;try{await api(`/notifications/${id}`,{method:"DELETE"});toast("Deleted");loadNotifications();}catch(e){toast(e.message);}}
async function ntTest(id,btn){btn.disabled=true;btn.textContent="Sending…";
  try{await api(`/notifications/test/${id}`,{method:"POST"});toast("Test sent");}
  catch(e){toast(e.message||"Test failed");}
  finally{btn.disabled=false;btn.textContent="Send test";}
}
function inviteUser(){
  const url=location.origin;
  _openModalRaw({title:"Share the sign-in link",okLabel:"Copy link",
    body:`<div style="font-size:14px;line-height:1.6;color:var(--text-2)">
      <p style="margin-bottom:10px">Anyone with this link can sign in and get their own private workspace. They can't see your data, and you can't see theirs.</p>
      <div style="display:flex;gap:8px;margin-bottom:12px"><input class="modal-field" id="invite-url" value="${esc(url)}" readonly aria-label="Sign-in link"></div>
      <p style="color:var(--text-3);font-size:13px">New accounts start as <b>analyst</b>. Once they've signed in they appear in this table, where you can change their role.</p>
    </div>`,
    getValue:()=>{const el=$("invite-url");el.select();try{navigator.clipboard.writeText(el.value);}catch{}toast("Invite link copied");return true;}});
}
async function loadAdmin(){
  const done=screenLoading("admin-rows","Loading user management…",4);
  try{
    const users=await api("/admin/users");if(!done())return;
    $("admin-rows").innerHTML=users.map(u=>`<tr>
      <td><b>${esc(u.email)}</b></td>
      <td style="color:var(--mut2);font-family:ui-monospace,Menlo,monospace;font-size:12px">${esc(u.user_id).slice(0,18)}…</td>
      <td><select class="write-only" aria-label="Role for ${esc(u.email)}" data-uid="${esc(u.user_id)}" onchange="setUserRole(this.dataset.uid,this.value)">
        ${["analyst","executive","admin"].map(r=>`<option ${u.role===r?"selected":""}>${r}</option>`).join("")}</select></td>
      <td style="color:var(--mut);white-space:nowrap">${esc(fmtDate(u.created_at))}</td></tr>`).join("")
      ||screenRow(screenState("No users yet","People who sign in appear here so you can manage their role.","inviteUser()","Share the sign-in link","empty",{writeOnly:true}),4);
  }catch(e){if(done())$("admin-rows").innerHTML=screenRow(screenError(e,"loadAdmin()"),4);}
}
async function setUserRole(uid,role){
  try{await api(`/admin/users/${uid}/role?role=${role}`,{method:"PUT"});toast("Role updated");}
  catch(e){toast(e.message);loadAdmin();}
}

/* ---------- datasets ---------- */
const DOMAIN_LABEL={fpa:"FP&A",marketing:"Marketing",ops:"Ops"};
let activeDatasetObj=null, activeDomain=null;
function datasetCaption(ds){return plural(ds.metric_count||0,"KPI")+" · imported "+fmtDate(ds.created_at);}
async function loadDatasets(){
  const d=await api("/datasets");
  activeDatasetId=d.active_id;
  try{localStorage.setItem("cb-ds",String(activeDatasetId??""));}catch{}
  datasetsCache=d.datasets;                       // for id -> name lookups (no strings in onclick)
  const active=d.datasets.find(x=>x.is_active);
  activeDatasetObj=active||null;
  displayCurrency=(active&&active.currency)||d.currency||"USD";
  // The active domain drives funnel rendering (marketing defines an ordered funnel).
  try{activeDomain=await api("/domain");}catch{activeDomain=null;}
  $("ws-name").textContent=active?datasetDisplayName(active.name):"No dataset";
  const rows=d.datasets.map(ds=>`
    <div class="ws-item">
      <button class="ws-choice" onclick="activateDataset(${esc(ds.id)})">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="${ds.is_active?'var(--accent)':'var(--mut2)'}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m7 14 4-4 3 3 5-5"/></svg>
      <span class="ws-info"><b>${esc(datasetDisplayName(ds.name))}</b><span>${esc(datasetCaption(ds))}</span></span></button>
      <span class="ws-kebab write-only">
        <button aria-label="Options for ${esc(datasetDisplayName(ds.name))}" aria-haspopup="true" aria-expanded="false" aria-controls="kmenu-${ds.id}" onclick="toggleKebab(event,${ds.id})">⋯</button>
        <div class="ws-kmenu" id="kmenu-${ds.id}">
          <button class="write-only" onclick="event.stopPropagation();renameDataset(${ds.id})">Rename</button>
          <button class="write-only" onclick="event.stopPropagation();setDatasetCurrency(${ds.id})">Currency (${esc(ds.currency||"USD")})</button>
          <button class="write-only danger" onclick="event.stopPropagation();deleteDataset(${ds.id})">Delete</button>
        </div>
      </span>
    </div>`).join("")||'<div class="ws-item">No datasets</div>';
  $("ws-menu").innerHTML=rows+`<button class="ws-newbtn write-only" onclick="event.stopPropagation();$('ws-menu').classList.remove('open');nav('import')">+ Import new dataset</button>`;
}
function dsName(id){const d=(datasetsCache||[]).find(x=>x.id===id);return d?datasetDisplayName(d.name):"this dataset";}
function toggleKebab(e,id){e.stopPropagation();
  document.querySelectorAll(".ws-kmenu").forEach(m=>{if(m.id!=="kmenu-"+id)m.classList.remove("open");});
  const open=$("kmenu-"+id).classList.toggle("open");
  document.querySelectorAll(".ws-kebab>button").forEach(b=>b.setAttribute("aria-expanded",String(open&&b.getAttribute("aria-controls")==="kmenu-"+id)));
  if(open)$("kmenu-"+id).querySelector("button")?.focus();
}
async function renameDataset(id){
  const cur=(datasetsCache.find(d=>d.id===id)||{}).name||"";
  const name=await modalInput("Rename dataset",{label:"Dataset name",value:cur,okLabel:"Rename",required:true});
  if(name==null||name===cur)return;
  try{await api(`/datasets/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})});toast("Renamed");await loadDatasets();}
  catch(e){toast(e.message);}
}
function currencyOptions(selected){
  return CURRENCIES.map(([c,m,name])=>`<option value="${c}" ${c===selected?"selected":""}>${esc(name)} (${esc(m.trim())})</option>`).join("");
}
async function setDatasetCurrency(id){
  const ds=(datasetsCache||[]).find(d=>d.id===id)||{},cur=ds.currency||"USD";
  const code=await _openModalRaw({title:"Currency",okLabel:"Save",
    body:`<label class="lbl" for="ds-currency">Show amounts in ${esc(dsName(id))} as</label>
      <select class="modal-field" id="ds-currency">${currencyOptions(cur)}</select>
      <p style="margin-top:10px;font-size:13px;color:var(--text-3)">This changes how amounts are labelled. Nothing is converted, and commentary already drafted keeps the symbol it was written with.</p>`,
    getValue:()=>$("ds-currency").value});
  if(code==null||code===cur)return;
  try{await api(`/datasets/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({currency:code})});
    toast(`Amounts now shown in ${code}`);await loadDatasets();if(id===activeDatasetId){overviewFor=null;await loadFacts();}}
  catch(e){toast(errMsg(e));}
}
/* ---------- Settings → Activity: the workspace's audit trail ---------- */
const ACT_VERB={approved:"Approved",changes_requested:"Asked for changes on",pending:"Sent for review",reopened:"Reopened",
  assign:"Assigned",generate:"Drafted",create:"Created",delete:"Deleted",sync:"Synced",send:"Sent",login:"Signed in",
  approve:"Approved",edit:"Edited",reject:"Rejected",update:"Updated"};
const ACT_THING={report:"commentary",dataset:"dataset",connector:"linked source",context:"context note",
  derived_kpi:"custom KPI",digest:"digest",user:"",workspace:"workspace"};
// "Approved commentary · Net Revenue, December 2025" from an audit row.
function describeActivity(a){
  const verb=ACT_VERB[a.action]||(a.action||"").replace(/_/g," ").replace(/^./,c=>c.toUpperCase());
  const thing=a.entity_type in ACT_THING?ACT_THING[a.entity_type]:(a.entity_type||"").replace(/_/g," ");
  const s=a.summary||{},about=a.metric?`${a.metric}, ${fmtPeriod(a.period,"long")}`
    :s.name||(a.entity_type==="digest"&&s.period?fmtPeriod(s.period,"long"):"")
    ||(a.action==="sync"?(s.status==="ok"?plural(s.rows||0,"row"):"Failed"):"");
  return {text:[verb,thing].filter(Boolean).join(" "),about};
}
function fmtWhen(iso){
  if(!iso)return "—";
  const s=String(iso),t=/[T ](\d{2}:\d{2})/.exec(s);if(!t)return fmtDate(s);
  const d=new Date(/Z|[+-]\d{2}:?\d{2}$/.test(s)?s.replace(" ","T"):s.replace(" ","T")+"Z");
  return isNaN(d)?fmtDate(s):`${fmtDate(s)} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
}
async function loadActivity(){
  const done=screenLoading("act-rows","Loading activity…",3);$("act-check").innerHTML="";
  try{
    const rows=await api("/audit");if(!done())return;
    $("act-rows").innerHTML=rows.length?rows.map(a=>{const d=describeActivity(a);
      return `<tr><td>${esc(fmtWhen(a.created_at))}</td><td>${esc(a.actor_email||(a.actor_id?"A team member":"Closebrief"))}</td><td>${esc(d.text)}${d.about?`<small>${esc(d.about)}</small>`:""}</td></tr>`;}).join("")
      :screenRow(screenState("No activity yet","Approvals, edits, imports and sends appear here as they happen.","","",),3);
  }catch(e){if(done())$("act-rows").innerHTML=screenRow(screenError(e,"loadActivity()"),3);}
}
async function verifyActivity(){
  const btn=$("act-verify"),box=$("act-check");btn.disabled=true;box.innerHTML='<div class="loading"><span class="spin"></span> Checking every entry…</div>';
  try{
    const r=await api("/audit/verify");
    box.innerHTML=r.ok
      ?`<div class="banner ok">All ${plural(r.checked,"entry","entries")} check out: nothing in this log has been edited or removed.</div>`
      :`<div class="banner err">${warnIcon()}Entry #${esc(r.first_bad_id)} doesn't match the chain: it, or an entry before it, was changed or removed outside Closebrief.</div>`;
  }catch(e){box.innerHTML=`<div class="banner err">${warnIcon()}${esc(errMsg(e))}</div>`;}
  finally{btn.disabled=false;}
}

/* ---------- Data and imports → keep a dataset in sync with a link ---------- */
// A Google Sheets URL becomes {kind:"google_sheets",sheet_id,gid}; any other https link is a CSV link.
function syncSourceFrom(link){
  const s=String(link||"").trim();if(!s)return null;
  const g=/docs\.google\.com\/spreadsheets\/d\/([A-Za-z0-9_-]+)/.exec(s);
  if(g){const gid=/[#&?]gid=(\d+)/.exec(s);return {kind:"google_sheets",config:{sheet_id:g[1],gid:gid?gid[1]:"0"}};}
  if(/^https:\/\//i.test(s))return {kind:"csv_url",config:{url:s}};
  return null;
}
async function loadSyncSources(){
  const box=$("sync-list");if(!box)return;
  try{
    const rows=await api("/connectors");
    box.innerHTML=rows.map(c=>{
      const last=c.last_sync_at?`Last synced ${fmtWhen(c.last_sync_at)}`:"Not synced yet";
      const status=c.last_status==="error"?` · <span class="bad">${esc(c.last_error||"Last sync failed")}</span>`:"";
      return `<div class="sync-row"><div class="grow"><b>${esc(c.name)}</b><small>${c.kind==="google_sheets"?"Google Sheet":"CSV link"} → dataset “${esc(c.dataset_name||c.name)}” · ${esc(last)}${status}</small></div>
        <button class="btn sm" data-act="sync-now" data-arg="${esc(c.id)}">Sync now</button>
        <button class="btn sm ghost" data-act="sync-remove" data-arg="${esc(c.id)}">Remove</button></div>`;}).join("");
  }catch(e){box.innerHTML=`<div class="banner err">${warnIcon()}${esc(errMsg(e))}</div>`;}
}
async function addSyncSource(){
  const src=syncSourceFrom($("sync-url").value),name=$("sync-name").value.trim();
  if(!src){toast("Paste a Google Sheet link or an https link to a CSV file");$("sync-url").focus();return;}
  if(!name){toast("Give the dataset a name");$("sync-name").focus();return;}
  const btn=$("sync-add");btn.disabled=true;
  try{
    const r=await api("/connectors",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...src,name,dataset_name:name})});
    $("sync-url").value="";$("sync-name").value="";await loadSyncSources();await syncSourceNow(r.id);
  }catch(e){toast(errMsg(e));}
  finally{btn.disabled=false;}
}
async function syncSourceNow(id){
  toast("Syncing…");
  try{
    const r=await api(`/connectors/${id}/sync`,{method:"POST"});
    if(r.status==="ok"){toast(`Synced ${plural(r.rows||0,"row")}`);await loadDatasets();await loadPeriods();}
    else toast(r.error||"Sync failed");
  }catch(e){toast(errMsg(e));}
  await loadSyncSources();
}
async function removeSyncSource(id){
  if(!await modalConfirm("Remove linked source","Stop syncing from this link? The dataset and its data stay; only the link is removed.",{okLabel:"Remove"}))return;
  try{await api(`/connectors/${id}`,{method:"DELETE"});toast("Linked source removed");}catch(e){toast(errMsg(e));}
  await loadSyncSources();
}
function toggleWs(e){e.stopPropagation();$("ws-menu").classList.toggle("open");}
document.addEventListener("click",()=>{$("ws-menu").classList.remove("open");$("export-menu")?.classList.remove("open");document.querySelectorAll(".ws-kmenu").forEach(m=>m.classList.remove("open"));});
/* ---------- workspaces (v4.0 tenancy) ----------
   Each person has one private workspace, resolved by the server from their
   membership. The client only remembers its id so requests stay scoped. */
async function loadWorkspaces(){
  if(!signedIn)return;
  let d;try{d=await api("/workspaces");}catch{return;}
  const list=d.workspaces||[];
  activeWorkspaceId=d.active_id!=null?d.active_id:((list[0]&&list[0].id)||null);
  try{if(activeWorkspaceId!=null)localStorage.setItem("cb-ws",String(activeWorkspaceId));}catch{}
}
// Keyboard shortcuts. Ignore while typing in a field.
document.addEventListener("keydown",e=>{
  // Overlays (shortcuts help / source viewer): Escape closes.
  if($("sc-back").classList.contains("open")){if(e.key==="Escape")$("sc-back").classList.remove("open");return;}
  if($("src-back").classList.contains("open")){if(e.key==="Escape")$("src-back").classList.remove("open");return;}
  if($("kpi-back").classList.contains("open")){if(e.key==="Escape")$("kpi-back").classList.remove("open");return;}
  if($("help-back").classList.contains("open")){if(e.key==="Escape")$("help-back").classList.remove("open");return;}
  if($("modal-back").classList.contains("open")){
    if(e.key==="Escape")closeModal();
    else if(e.key==="Enter"&&(e.target.tagName||"").toLowerCase()==="input"){e.preventDefault();$("modal-ok").click();}
    return;
  }
  if(document.body.classList.contains("sidebar-open")&&sidebarMedia.matches){
    if(e.key==="Escape"){e.preventDefault();closeSidebar(true);return;}
    if(e.key==="Tab"){
      const items=sidebarFocusable();if(!items.length)return;
      const first=items[0],last=items[items.length-1];
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
    }
  }
  // While the palette is open, route nav keys to it.
  if($("cmdk-back").classList.contains("open")){paletteKey(e);return;}
  // Ctrl/Cmd+K opens the palette from anywhere (even while typing).
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();if($("app-root").style.display!=="none")openPalette();return;}
  const tag=(e.target.tagName||"").toLowerCase();
  if(tag==="input"||tag==="textarea"||tag==="select"||e.metaKey||e.ctrlKey||e.altKey)return;
  const appOn=$("app-root").style.display!=="none";
  if(e.key==="d"){toggleTheme();}
  else if(e.key==="?"){if(appOn)$("sc-back").classList.toggle("open");}
  else if(!appOn){return;}
  else if(e.key==="/"){e.preventDefault();$("search")?.focus();}
  else if((currentUser&&currentUser.role==="viewer")){return;}   // viewer: no write shortcuts
  else if(e.key==="g"){generateAll();}
  else if(e.key==="e"){toggleExport(e);}
});
async function activateDataset(id){
  if(!await leaveImport())return;
  closeSidebar();$("statrow").innerHTML="";$("cards").innerHTML=skeletonCards();  // brief loading state
  const prev=activeDatasetId;activeDatasetId=id;   // later requests carry the new dataset
  try{await api(`/datasets/${id}/activate`,{method:"POST"});dropStaleImport();toast("Switched dataset");await loadPeriods();}
  catch(e){activeDatasetId=prev;toast("Couldn't switch dataset: "+e.message);}
}
async function deleteDataset(id){
  const ok=await modalConfirm("Delete dataset",`Delete "${dsName(id)}" and all its data? This can't be undone.`,{okLabel:"Delete"});
  if(!ok)return;
  try{
    await api(`/datasets/${id}`,{method:"DELETE"});
    toast("Dataset deleted");
    await loadDatasets();
    await loadPeriods();
    nav(currentPlace.place||currentPlace.tab||"overview",{fromRoute:true});
  }catch(e){toast("Delete failed: "+e.message);}
}

/* ---------- periods & facts ---------- */
let granularity="month";
function setGranularity(g){
  if(g===granularity)return Promise.resolve();granularity=g;
  document.querySelectorAll("#gran-seg button").forEach(b=>{b.classList.toggle("active",b.dataset.g===g);b.setAttribute("aria-pressed",String(b.dataset.g===g));});
  $("closes-label").textContent={month:"Closes",quarter:"Quarters",year:"Years"}[g];showAllCloses=false;
  return loadPeriods();
}
function stepPeriod(dir){
  const sel=$("period-select"),i=sel.selectedIndex+dir;
  if(i<0||i>=sel.options.length)return;
  sel.selectedIndex=i;loadFacts();syncStepButtons();
}
function syncStepButtons(){
  const sel=$("period-select");if(!sel)return;
  const btns=document.querySelectorAll(".period-ctl .pnav");if(btns.length<2)return;
  const atStart=sel.selectedIndex<=0,atEnd=sel.selectedIndex>=sel.options.length-1;
  btns[0].style.opacity=atStart?.35:1;btns[0].style.cursor=atStart?"default":"pointer";btns[0].disabled=atStart;
  btns[1].style.opacity=atEnd?.35:1;btns[1].style.cursor=atEnd?"default":"pointer";btns[1].disabled=atEnd;
}
function groupPeriodsByYear(periods){
  // Build <optgroup> by year for quarter/year; flat for month.
  if(granularity==="year")return periods.map(periodOption).join("");
  const byYear={};periods.forEach(p=>{const y=p.slice(0,4);(byYear[y]=byYear[y]||[]).push(p);});
  return Object.keys(byYear).sort().map(y=>`<optgroup label="${y}">${byYear[y].map(periodOption).join("")}</optgroup>`).join("");
}
async function loadPeriods(){
  await loadDatasets();
  const periods=await api(`/periods?granularity=${granularity}`);
  allPeriods=periods;overviewFor=null;boardPackFor=null;
  const sel=$("period-select");
  sel.innerHTML=granularity==="month"?periods.map(periodOption).join(""):groupPeriodsByYear(periods);
  if(periods.length){
    // Keep the open close when it still exists (switching M/Q/Y or dataset), else open the newest.
    const want=parseRoute(location.hash),keep=want&&want.period&&periods.includes(want.period)?want.period:null;
    sel.value=keep||periods[periods.length-1];currentPeriod=sel.value;renderCloses();await loadFacts();loadCloseMeta();}
  else{
    // Empty workspace: show a centered import CTA on the dashboard, don't jump away.
    currentPeriod=null;currentFacts=[];shownFacts=[];detailFact=null;renderCloses();renderMonthHead();
    $("dash-sub").textContent="No data yet";$("statrow").innerHTML="";$("filter-pills").innerHTML="";$("cat-pills").innerHTML="";$("dash-title").textContent="Dashboard";
    $("cards").innerHTML=`<div class="empty-state" style="grid-column:1/-1;padding:70px 20px">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="width:52px;height:52px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/></svg>
      <div style="font-size:17px;font-weight:800;color:var(--ink);margin:14px 0 6px">Import your financials</div>
      <div style="max-width:34ch;margin:0 auto 18px">Drop in a CSV or Excel export — any layout. We detect your columns and draft variance commentary in minutes.</div>
      <button class="btn primary write-only" onclick="nav('datahub')" style="font-size:14px;padding:11px 20px">Import your first file</button>
    </div>`;
  }
}
// Value stays ISO (used by the API and the URL); the label is what people read.
function periodOption(p){return `<option value="${esc(p)}">${esc(fmtPeriod(p))}</option>`;}
function skeletonCards(n=6){
  const one=`<div class="sk-card"><div class="sk sk-line" style="width:45%"></div>
    <div class="sk" style="height:30px;width:60%"></div>
    <div style="display:flex;gap:8px"><div class="sk" style="height:22px;width:78px"></div><div class="sk" style="height:22px;width:88px"></div></div>
    <div class="sk sk-line" style="width:100%"></div><div class="sk sk-line" style="width:92%"></div><div class="sk sk-line" style="width:70%"></div></div>`;
  return Array(n).fill(one).join("");
}
let _factsSlowT=null;
async function loadFacts(){
  currentPeriod=$("period-select").value;syncStepButtons();
  $("dash-loading").style.display="none";$("statrow").innerHTML="";$("cards").innerHTML=skeletonCards();
  // Say what is happening; large datasets compute for a while on first load.
  $("dash-title").textContent=fmtPeriodTitle(currentPeriod);
  $("dash-sub").textContent=`Loading KPIs for ${fmtPeriod(currentPeriod,"long")}…`;$("dash-fresh").textContent="";
  clearTimeout(_factsSlowT);
  const slowFor=currentPeriod;
  _factsSlowT=setTimeout(()=>{if(currentPeriod===slowFor&&$("dash-sub").textContent.startsWith("Loading"))
    $("dash-sub").textContent="Still computing KPIs — the first load of a large dataset can take up to a minute.";},4000);
  try{
    currentFacts=await api(`/facts?period=${encodeURIComponent(currentPeriod)}&granularity=${granularity}`);
    clearTimeout(_factsSlowT);
    const cats=[...new Set(currentFacts.map(f=>f.category||"Uncategorized"))].sort();
    assignCatSlots(cats);
    renderCatPills(cats);
    renderCards();
    const anom=currentFacts.filter(f=>f.is_anomaly).map(f=>f.metric);
    $("dash-title").textContent=fmtPeriodTitle(currentPeriod);
    if($("v-dashboard").style.display!=="none")setDocTitle(fmtPeriodTitle(currentPeriod));
    const aggNote=granularity==="month"?"":" · commentary is drafted monthly";
    $("dash-sub").innerHTML=`${currentFacts.length} ${currentFacts.length===1?"KPI":"KPIs"} · <b>${anom.length?plural(anom.length,"anomaly","anomalies"):"no anomalies"}</b>${aggNote}`;
    const anomKey=`anomaly:${currentPeriod}`;
    if(anom.length&&!bannerDismissed(anomKey)){
      $("anomaly-banner").style.display="";
      $("anomaly-banner").innerHTML=warnIcon()+`<span>Anomalous movement: ${anom.map(esc).join(", ")}. Open a card for the full story.</span>`+dismissX(anomKey,"anomaly-banner");
    }else $("anomaly-banner").style.display="none";
    updateGenButton();maybePromptGenerate();setFreshness();
    renderCloses();renderMonthHead();boardPackFor=null;
    if($("v-overview").style.display!=="none")renderOverview();
    loadConflicts();renderFunnel();
  }catch(e){
    clearTimeout(_factsSlowT);
    $("statrow").innerHTML="";$("dash-sub").textContent="Couldn't load data";
    $("cards").innerHTML=`<div class="empty-state" style="grid-column:1/-1;padding:56px 20px">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:40px;height:40px"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
      <div style="margin:12px 0 4px;font-weight:700;color:var(--ink2)">Couldn't load KPIs</div>
      <div style="font-size:13px;margin-bottom:16px">${esc(e.message||"Something went wrong.")}</div>
      <button class="btn primary" onclick="loadFacts()">Retry</button></div>`;
  }finally{$("dash-loading").style.display="none";refreshReviewBadge();}
}
function setFreshness(){
  const el=$("dash-fresh");if(!el)return;
  const sel=$("period-select"),latest=sel&&sel.options.length?sel.options[sel.options.length-1].value:currentPeriod;
  const imported=activeDatasetObj&&activeDatasetObj.created_at?fmtDate(activeDatasetObj.created_at):null;
  const narr=currentFacts.filter(f=>f.has_data&&f.narrative).length,tot=currentFacts.filter(f=>f.has_data).length;
  const bits=[`Data through ${fmtPeriod(latest)}`];
  if(imported)bits.push(`imported ${imported}`);
  if(tot)bits.push(narr===tot?"commentary up to date":`${narr} of ${tot} drafted`);
  el.textContent=bits.join(" · ");
}
function maybePromptGenerate(){
  // Offer (never force) generation when a month period has data but no narratives.
  const el=$("import-banner");if(!el||demoSession)return;
  const withData=currentFacts.filter(f=>f.has_data),none=granularity==="month"&&withData.length&&withData.every(f=>!f.narrative);
  if(!none){if(el.classList.contains("info"))el.style.display="none";return;}
  if(el.style.display==="none"){
    el.style.display="";el.className="banner info";
    el.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h7l-1 8 10-12h-7z"/></svg>
      <span>No narratives for ${esc(fmtPeriod(currentPeriod,"long"))} yet.</span>
      <button class="btn sm primary write-only" style="margin-left:auto" onclick="$('import-banner').style.display='none';generateAll()">Draft commentary</button>
      <button class="ico" style="border:none" onclick="$('import-banner').style.display='none'" aria-label="Dismiss">${XICON}</button>`;
  }
}
function dismissKey(type){return `cb-dismiss:${(currentUser&&currentUser.email)||"anon"}:${activeDatasetId}:${type}`;}
function bannerDismissed(type){try{return localStorage.getItem(dismissKey(type))==="1";}catch{return false;}}
function dismissX(type,elId){return `<button class="ico" style="border:none;margin-left:auto" aria-label="Dismiss" data-act="dismiss" data-arg="${esc(dismissKey(type))}" data-target="${esc(elId)}">${XICON}</button>`;}
async function loadConflicts(){
  try{
    const cf=await api("/context/conflicts");const el=$("conflict-banner");
    const c=cf[0];const key=c?`conflict:${esc(c.doc_a.title)}|${esc(c.doc_b.title)}`:"";
    if(!cf.length||bannerDismissed(key)){el.style.display="none";return;}
    const f=c.figures[0];el.style.display="";
    el.innerHTML=warnIcon()+`<span>Context conflict: "${esc(c.doc_a.title)}" and "${esc(c.doc_b.title)}" cite different figures`
      +(f?` (${fmt(f.a,"USD")} vs ${fmt(f.b,"USD")})`:"")+`. Narratives use the most recent (${esc(c.most_recent)}).</span>`+dismissX(key,"conflict-banner");
  }catch{$("conflict-banner").style.display="none";}
}
const warnIcon=()=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/></svg>`;

/* ---------- funnel (Phase 1: stage-over-stage acquisition funnel) ---------- */
async function renderFunnel(){
  const el=$("funnel-panel");if(!el)return;
  // Only the marketing (or any funnel-defining) domain shows a funnel.
  if(!activeDomain||!(activeDomain.funnel&&activeDomain.funnel.length)||granularity!=="month"){el.style.display="none";el.innerHTML="";return;}
  let f;
  try{f=await api(`/funnel?period=${encodeURIComponent(currentPeriod)}`);}
  catch{el.style.display="none";return;}
  const stages=(f.stages||[]).filter(s=>s.value!=null);
  if(stages.length<2){el.style.display="none";return;}
  const max=Math.max(...stages.map(s=>s.value))||1;
  const ppChip=v=>v==null?"":`<span class="pp ${v>0?"good":v<0?"bad":""}">${v>0?"+":""}${v}pp</span>`;
  const cards=stages.map(s=>{
    const leak=s.name===f.biggest_dropoff_stage?" leak":"";
    const conv=s.conversion_from_prev!=null
      ? `<span class="fconv">${s.conversion_from_prev}% of prev ${ppChip(s.conversion_mom_pp)}</span>` : `<span class="fconv">entry</span>`;
    return `<div class="fstage${leak}">
      <span class="fs-name">${esc(s.name)}</span>
      <span class="fs-val nbi-num">${(+s.value).toLocaleString()}</span>
      <div class="bar" style="width:${Math.max(8,Math.round(s.value/max*100))}%"></div>
      ${conv}</div>`;
  }).join("");
  const leakLine=f.biggest_dropoff_stage
    ? `Biggest leak into <b>${esc(f.biggest_dropoff_stage)}</b>` : "";
  el.style.display="";
  el.innerHTML=`<div class="funnel">
    <div class="funnel-head">
      <div><h3>Acquisition funnel</h3><div class="fh-sub">${esc(fmtPeriodTitle(currentPeriod).replace(" close","").replace(" overview",""))} · overall ${f.overall_conversion!=null?f.overall_conversion+"% end-to-end":"—"}</div></div>
      <div class="fh-sub">${leakLine} ${activeDomain.narrative_style?'· <button class="write-only txtact acc" onclick="explainFunnel(this)">Explain funnel</button>':""}</div>
    </div>
    <div class="funnel-stages">${cards}</div>
    <div class="funnel-narr" id="funnel-narr" style="display:none"></div>
  </div>`;
}
async function explainFunnel(btn){
  const box=$("funnel-narr");if(!box)return;
  btn.disabled=true;btn.textContent="Explaining…";
  box.style.display="";box.innerHTML='<span class="spin"></span> Writing funnel summary…';
  try{
    const r=await api("/funnel/narrative",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({period:currentPeriod})});
    box.textContent=r.narrative||"No summary available.";
  }catch(e){box.innerHTML=`<span style="color:var(--red)">${esc(e.message)}</span>`;}
  finally{btn.disabled=false;btn.textContent="Explain funnel";}
}

/* The signature mark: the auditor's tick. One seal wherever numbers are verified. */
const XICON='<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
// The pencil tick from the home page's legend: figures computed in code.
const TICK='<svg viewBox="0 0 26 22" fill="none" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 12.2c1.9 1.4 3.6 3.3 5.2 5.6C12.4 11 16.8 6.2 22.6 3.4"/></svg>';
const seal=ok=>ok
  ?`<span class="seal" title="Each number matches a computed figure or a cited note as written, and every rise or fall matches the data">${TICK}Figures checked</span>`
  :`<span class="seal failed" title="A number doesn't match the computed figures, or calls a fall a rise. Review before sharing"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>Needs review</span>`;
const DOCICON='<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>';
/* Icon registry for JS-rendered markup. The bell mirrors the static sidebar-nav
   bell — change both together. */
const ICON_PATHS={
  bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  mail:'<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 5L2 7"/>',
  chat:'<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  link:'<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  trash:'<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'};

/* ---------- cost & usage ---------- */
let costsData=null;
async function loadCosts(){
  const done=screenLoading("costs-state","Loading cost and usage…");$("costs-content").style.display="none";
  try{
    const c=await api("/costs");if(!done())return;costsData=c;
    if(!(c.total_llm_calls||c.total_cost_usd||(c.by_day||[]).length||Object.keys(c.latency||{}).length||(c.cache||{}).hits||(c.cache||{}).misses||(c.alert_latency||{}).n)){
      $("costs-state").innerHTML=screenState("No usage recorded yet","Track AI spend, request latency and cache efficiency as your team uses Closebrief.","nav('dashboard')","Open the dashboard");return;
    }
    $("costs-state").innerHTML="";$("costs-content").style.display="";
    // Stat tiles
    const cache=c.cache||{},hr=cache.hit_rate;
    const lat=c.latency||{},p95s=Object.values(lat).map(x=>x.p95_ms).filter(v=>v!=null);
    const worstP95=p95s.length?Math.max(...p95s):null;
    const al=c.alert_latency||{};
    const svg=p=>`<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
    const tile=(lbl,path,col,val,hint)=>`<div class="stat rise" style="--sc:${esc(col)}"><span class="stat-lbl">${svg(path)}${esc(lbl)}</span><span class="stat-val nbi-num">${esc(val)}</span><span class="stat-hint">${esc(hint)}</span></div>`;
    $("costs-stats").innerHTML=[
      tile("Total spend",'<path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',"var(--green)",fmt(c.total_cost_usd,"USD","USD"),"across all LLM calls"),
      tile("LLM calls",'<path d="M13 2 3 14h7l-1 8 10-12h-7z"/>',"var(--accent)",c.total_llm_calls||0,"generations + digests"),
      tile("Cache hit rate",'<path d="M21 12a9 9 0 1 1-6.2-8.5"/><path d="M21 3v6h-6"/>',hr>=0.5?"var(--green)":"var(--amber)",hr==null?"—":Math.round(hr*100)+"%",`${cache.hits||0} hits · ${cache.misses||0} misses`),
      tile("Worst p95 latency",'<path d="M12 8v4l3 2"/><circle cx="12" cy="12" r="9"/>',worstP95>2000?"var(--red)":"var(--accent)",worstP95==null?"—":Math.round(worstP95)+" ms","slowest endpoint"),
      tile("Alert latency p95",'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',al.within_30s===false?"var(--red)":"var(--green)",al.p95_ms==null?"—":(al.p95_ms/1000).toFixed(1)+" s",al.n?`${al.n} alerts · target < 30s`:"no alerts yet"),
    ].join("");
    // Spend-by-day chart (aggregate endpoints per day)
    const byDay={};(c.by_day||[]).forEach(r=>{byDay[r.day]=(byDay[r.day]||0)+(+r.cost_usd||0);});
    const days=Object.keys(byDay).sort();
    ensureCanvas("c-spend");const box=$("box-c-spend");
    if(!days.length){if(chartReg["c-spend"]){chartReg["c-spend"].destroy();delete chartReg["c-spend"];}box.innerHTML='<div class="loading">No LLM spend recorded yet.</div>';}
    else{const th=chartTheme();
      mkChart("c-spend",{type:"bar",
        data:{labels:days.map(d=>fmtDate(d)),datasets:[{label:"Spend",data:days.map(d=>byDay[d]),backgroundColor:th.accent,borderRadius:5,borderSkipped:false,maxBarThickness:40}]},
        options:{responsive:true,maintainAspectRatio:false,
          plugins:{legend:{display:false},tooltip:tooltipCfg("USD",{label:c=>" "+fmt(c.parsed.y,"USD")})},
          scales:{y:{grid:{color:th.grid},ticks:{color:th.mut,maxTicksLimit:5,callback:v=>fmt(v,"USD","USD")},border:{display:false}},
            x:{grid:{display:false},ticks:{color:th.mut,maxRotation:0,autoSkip:true,maxTicksLimit:8},border:{color:th.grid}}}}});
    }
    // Latency table
    const lrows=Object.entries(lat);
    $("costs-latency").innerHTML=lrows.length?`<table class="ctx-table" style="margin-top:8px"><thead><tr><th>Endpoint</th><th style="text-align:right">p50</th><th style="text-align:right">p95</th><th style="text-align:right">n</th></tr></thead><tbody>${lrows.map(([k,v])=>`<tr><td style="font-family:ui-monospace,monospace;font-size:12px">${esc(k)}</td><td style="text-align:right" class="nbi-num">${esc(Math.round(v.p50_ms))} ms</td><td style="text-align:right" class="nbi-num">${esc(Math.round(v.p95_ms))} ms</td><td style="text-align:right" class="nbi-num">${esc(v.n)}</td></tr>`).join("")}</tbody></table>`:'<div class="loading">No latency samples yet this session.</div>';
    // Endpoint spend table
    const rows=c.by_day||[];
    $("costs-table").innerHTML=rows.length?`<table class="ctx-table"><thead><tr><th>Day</th><th>Endpoint</th><th style="text-align:right">Calls</th><th style="text-align:right">Prompt tok</th><th style="text-align:right">Completion tok</th><th style="text-align:right">Cost</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(fmtDate(r.day))}</td><td style="font-family:ui-monospace,monospace;font-size:12px">${esc(r.endpoint)}</td><td style="text-align:right" class="nbi-num">${esc(r.calls)}</td><td style="text-align:right" class="nbi-num">${esc(r.prompt_tokens||0)}</td><td style="text-align:right" class="nbi-num">${esc(r.completion_tokens||0)}</td><td style="text-align:right" class="nbi-num">${esc(fmt(r.cost_usd,"USD","USD"))}</td></tr>`).join("")}</tbody></table>`:'<div class="loading">No spend recorded yet.</div>';
  }catch(e){if(done())$("costs-state").innerHTML=screenError(e,"loadCosts()");}
}

function deltaChip(label,v,downGood,text){
  const p=text!==undefined?text:pct(v);if(p===null||v==null)return`<span class="delta flat">${label} –</span>`;
  const good=downGood?v<0:v>0,cls=v===0?"flat":good?"good":"bad";
  return`<span class="delta ${cls}">${label} ${p}</span>`;
}
function narrativeBlock(f,q,i){
  if(generatingSet.has(f.metric))
    return `<div class="narr"><div class="sk sk-line" style="width:100%;margin-bottom:5px"></div><div class="sk sk-line" style="width:94%;margin-bottom:5px"></div><div class="sk sk-line" style="width:72%"></div></div>`;
  if(f.aggregated)return "";   // one note in the page sub-line, not repeated per card
  const narrState=!f.narrative?"muted":f.review_status==="approved"?"approved":"draft";
  let html=`<div class="narr ${narrState}">${f.narrative?hl(f.narrative,q):"No narrative yet — generate one to explain this movement."}</div>`;
  if(f.faithfulness==="failed")
    // The guard caught an unverifiable number. Surface it — do NOT hide it.
    html+=`<div class="banner err" style="margin:8px 0 0;font-size:12px;padding:8px 10px">${warnIcon()}<span>A number doesn't match the computed figures. Review it before approving.</span><button class="btn sm write-only" style="margin-left:auto;padding:3px 9px" onclick="event.stopPropagation();retryCard(${i})">Retry</button></div>`;
  return html;
}
// Index-based wrappers so no user string is interpolated into inline onclick.
function genCard(i,btn){const f=shownFacts[i];if(f)genOne(f.metric,btn);}
function retryCard(i){const f=shownFacts[i];if(f)retryGen(f.metric);}
async function retryGen(metric){
  const gp=currentPeriod,gd=activeDatasetId;
  generatingSet.add(metric);renderCards();
  try{const r=await api("/generate-insight",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({metric,period:gp,force:true})});
    if(currentPeriod===gp&&activeDatasetId===gd)applyGenerated(r);}
  catch(e){toast("Retry failed: "+e.message);}
  generatingSet.delete(metric);renderCards();
}
function streakBadge(s){
  if(!s)return"";
  const up=s.direction==="growing";
  return`<span class="delta ${up?"good":"bad"}" title="${esc(s.start_period)} → ${esc(s.end_period)}">${s.months}-month ${up?"rise":"decline"}</span>`;
}

// The review order of the queue, and the words each group goes by.
const QUEUE_GROUPS=[["exception","Exceptions"],["review","Needs review"],["approved","Approved"],["nodata","No data this period"]];
function queueGroup(f){
  if(!f.has_data)return "nodata";
  if(f.is_anomaly||f.faithfulness==="failed")return "exception";
  if(f.review_status==="approved")return "approved";
  return "review";
}
function renderStatrow(facts){
  const el=$("statrow");if(!el)return;
  const withData=facts.filter(f=>f.has_data);
  if(!withData.length){el.innerHTML="";return;}
  const n=g=>withData.filter(f=>queueGroup(f)===g).length;
  const drafted=withData.filter(f=>f.narrative).length;
  el.innerHTML=`<span class="qs exc"><b class="nbi-num">${n("exception")}</b> ${n("exception")===1?"exception":"exceptions"}</span>`
    +`<span class="qs rev"><b class="nbi-num">${n("review")}</b> to review</span>`
    +`<span class="qs ok"><b class="nbi-num">${n("approved")}</b> approved</span>`
    +`<span class="qs drafted">${drafted}/${withData.length} drafted</span>`;
}
let shownFacts=[];
function renderPills(){
  const cat=catFilter,pills=[];
  if(cat)pills.push(`Category: ${esc(cat)}<button onclick="clearCatFilter()" aria-label="Clear category filter">${XICON}</button>`);
  if(searchQuery)pills.push(`Search: “${esc(searchQuery)}”<button onclick="clearSearch()" aria-label="Clear search">${XICON}</button>`);
  $("filter-pills").innerHTML=pills.map(p=>`<span class="fpill">${p}</span>`).join("");
}
// Enter/Space activates non-native controls marked role="button" (queue rows,
// source and metric chips, grounded sentences), the same way a <button> would.
document.addEventListener("keydown",e=>{
  const t=e.target;
  if((e.key==="Enter"||e.key===" ")&&t&&t.getAttribute&&t.getAttribute("role")==="button"&&!/^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(t.tagName)){e.preventDefault();t.click();}
});
// Cards act as links: Enter/Space opens, but only when the card itself has focus
// (not one of its inner Accept/Edit/Generate buttons).
function cardKey(e,i){if(e.target===e.currentTarget&&(e.key==="Enter"||e.key===" ")){e.preventDefault();openDetail(i);}}
function clearCatFilter(){setCat("");}
function clearSearch(){searchQuery="";const s=$("search");if(s)s.value="";renderCards();}
function renderCards(){
  const cat=catFilter;
  let facts=cat?currentFacts.filter(f=>(f.category||"Uncategorized")===cat):currentFacts;
  if(searchQuery)facts=facts.filter(f=>factMatches(f,searchQuery));
  // Review order: exceptions first, approved and empty KPIs last; stable within a group.
  const order=Object.fromEntries(QUEUE_GROUPS.map(([g],i)=>[g,i]));
  facts=facts.map((f,i)=>[f,i]).sort((x,y)=>order[queueGroup(x[0])]-order[queueGroup(y[0])]||x[1]-y[1]).map(x=>x[0]);
  shownFacts=facts;
  renderStatrow(facts);renderPills();renderBulkApprove();
  if(!currentFacts.length){$("cards").innerHTML=`<div class="empty-state">No KPIs to show. Select KPIs during import, or switch dataset.</div>`;return;}
  if(!facts.length){$("cards").innerHTML=`<div class="empty-state">No KPIs match ${searchQuery?`“${esc(searchQuery)}”`:"these filters"}. <button class="linkbtn" onclick="clearSearch();clearCatFilter()">Clear filters</button></div>`;return;}
  const q=searchQuery;let html="",last=null;
  facts.forEach((f,i)=>{
    const g=queueGroup(f);
    if(g!==last){
      html+=`<div class="q-grp">${QUEUE_GROUPS.find(x=>x[0]===g)[1]}<span class="nbi-num">${facts.filter(x=>queueGroup(x)===g).length}</span></div>`;
      last=g;
    }
    html+=queueRow(f,i,q);
  });
  $("cards").innerHTML=html;
  autoSelect();
}
// One queue row: status dot, name, the variance that matters, and the state in words.
function queueRow(f,i,q){
  const g=queueGroup(f);
  if(!f.has_data)return `<div class="qi nodata"><i class="dot" aria-hidden="true"></i><span class="qn">${hl(f.metric,q)}</span><span class="qsub">No data for ${esc(fmtPeriod(currentPeriod))}</span></div>`;
  const plan=f.deltas.budget_var_pct,v=plan!=null?plan:f.deltas.mom_pct;
  const vLabel=plan!=null?"vs plan":priorPeriodLabel();
  const good=v==null||v===0?null:(f.direction_good==="down"?v<0:v>0);
  const tone=good===null?"":good?"c-ok":"c-exc";
  const state=generatingSet.has(f.metric)?"Drafting…"
    :g==="exception"?(f.is_anomaly?"Anomaly":"Figures need review")
    :g==="approved"?"Approved":f.narrative?"Draft ready":"Not drafted";
  const sel=!!(detailFact&&detailFact.metric===f.metric);
  return `<div class="qi ${g}${sel?" on":""}" data-i="${i}" onclick="openDetail(${i})" onkeydown="cardKey(event,${i})" tabindex="0" role="link"${sel?' aria-current="true"':""} aria-label="${esc(f.metric)}, ${esc(f.unavailable?"no value":fmt(f.value,f.unit))}, ${esc(state)}">
    <i class="dot" aria-hidden="true"></i><span class="qn">${hl(f.metric,q)}</span>
    <span class="qv nbi-num ${tone}">${v==null?"–":deltaText(f,plan!=null?"budget":"mom")||pct(v)}</span>
    <span class="qsub"><span class="nbi-num">${f.unavailable?"—":fmt(f.value,f.unit)}</span> · ${vLabel} · ${esc(state)}</span></div>`;
}
function markQueueSelection(){
  document.querySelectorAll("#cards .qi[data-i]").forEach(el=>{
    const f=shownFacts[+el.dataset.i],on=!!(f&&detailFact&&f.metric===detailFact.metric);
    el.classList.toggle("on",on);
    if(on)el.setAttribute("aria-current","true");else el.removeAttribute("aria-current");
  });
}
// Wide screens always show a document: keep the open KPI (refreshed to this
// period's figures) or open the first one in review order.
const wideWorkspace=window.matchMedia?window.matchMedia("(min-width: 901px)"):{matches:true};
function autoSelect(){
  if(!wideWorkspace.matches||$("v-dashboard").style.display==="none")return;
  const cur=detailFact?shownFacts.findIndex(f=>f.metric===detailFact.metric&&f.has_data):-1;
  if(cur>=0){if(shownFacts[cur]!==detailFact)openDetail(cur,{fromRoute:true});return;}
  const first=shownFacts.findIndex(f=>f.has_data);
  if(first>=0)openDetail(first,{fromRoute:true});
}

/* ---------- charts ---------- */
function sparkline(trend,dir,w=110,h=34,unit=""){
  const vals=trend.map(t=>t.value).filter(v=>v!=null);if(vals.length<2)return"";
  const bvals=trend.map(t=>t.budget).filter(v=>v!=null);
  const all=vals.concat(bvals),min=Math.min(...all),max=Math.max(...all),sp=(max-min)||1;
  const X=i=>2+i*(w-6)/(trend.length-1),Y=v=>h-3-((v-min)/sp)*(h-8);
  const monthsApart=(a,b)=>{const[ay,am]=a.split("-").map(Number),[by,bm]=b.split("-").map(Number);return by*12+bm-(ay*12+am);};
  const line=key=>trend.map((t,i)=>{if(t[key]==null)return"";const cont=i&&trend[i-1][key]!=null&&monthsApart(trend[i-1].period,t.period)===1;return`${cont?"L":"M"}${X(i).toFixed(1)},${Y(t[key]).toFixed(1)}`;}).join(" ");
  const areaPts=trend.map((t,i)=>t.value!=null?`${X(i).toFixed(1)},${Y(t.value).toFixed(1)}`:"").filter(Boolean);
  const area=`M${areaPts[0]} `+trend.map((t,i)=>t.value!=null?`L${X(i).toFixed(1)},${Y(t.value).toFixed(1)}`:"").join(" ")+` L${X(trend.length-1).toFixed(1)},${h} L${X(0).toFixed(1)},${h} Z`;
  const stroke=trendColor(trend,dir);const li=trend.length-1;
  const first=trend.find(t=>t.value!=null),last=trend[li];
  const tip=`Actual — solid · Plan — dashed\n${first?first.period+": "+fmt(first.value,unit)+" → ":""}${last.period}: ${fmt(last.value,unit)}`;
  return`<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="flex:none"><title>${esc(tip)}</title>
    <path d="${area}" fill="${stroke}" opacity="0.08"/>
    <path d="${line("budget")}" stroke="var(--mut2)" stroke-width="1.6" stroke-dasharray="3 3" stroke-linecap="round" fill="none" opacity="0.7"/>
    <path d="${line("value")}" stroke="${stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
    <circle cx="${X(li).toFixed(1)}" cy="${Y(trend[li].value).toFixed(1)}" r="2.6" fill="${stroke}"/></svg>`;
}
function trendColor(trend,dir){
  const v=trend.map(t=>t.value).filter(x=>x!=null);if(v.length<2)return"var(--mut2)";
  const up=v[v.length-1]>=v[0],good=dir==="down"?!up:up;return good?"var(--green2)":"var(--red)";
}

/* ---- Chart.js: interactive, theme-aware detail charts ---- */
const chartReg={};                       // canvasId -> Chart instance
function cssv(name){return getComputedStyle(document.documentElement).getPropertyValue(name).trim();}
function chartTheme(){
  return {ink:cssv("--ink2")||"#33413a",mut:cssv("--mut2")||"#8a948c",
    grid:cssv("--b08")||"rgba(22,33,27,.09)",accent:cssv("--accent")||"#1e6e50",
    green:cssv("--green2")||"#25835f",red:cssv("--red")||"#b42e2c",card:cssv("--card")||"#fff"};
}
if(window.Chart){
  Chart.defaults.font.family="'Schibsted Grotesk',system-ui,-apple-system,sans-serif";
  Chart.defaults.font.size=11;
  Chart.defaults.animation.duration=matchMedia("(prefers-reduced-motion: reduce)").matches?0:600;
  Chart.defaults.animation.easing="easeOutQuart";
}
function ensureCanvas(id){
  // A prior "no data" state may have replaced the canvas with text; recreate it.
  if($(id))return;
  const box=$("box-"+id);if(!box)return;
  box.innerHTML=`<canvas id="${id}"></canvas>`;
}
function mkChart(id,cfg){
  if(chartReg[id]){chartReg[id].destroy();delete chartReg[id];}
  const cv=$(id);if(!cv||!window.Chart)return;
  chartReg[id]=new Chart(cv.getContext("2d"),cfg);
}
function tooltipCfg(unit,labeler){
  const th=chartTheme();
  return {enabled:true,backgroundColor:th.card,titleColor:th.ink,bodyColor:th.ink,
    borderColor:th.grid,borderWidth:1,padding:9,cornerRadius:8,displayColors:true,boxPadding:4,
    titleFont:{weight:"700"},bodyFont:{weight:"600"},
    callbacks:labeler||{label:c=>` ${c.dataset.label||""}: ${fmt(c.parsed.y,unit)}`}};
}
function renderTrendChart(trend,unit){
  ensureCanvas("c-trend");const box=$("box-c-trend"),note=$("d-trend-axis");if(note)note.textContent="";
  const has=trend.some(t=>t.value!=null||t.budget!=null);
  const months=trend.filter(t=>t.value!=null).length;
  $("d-trend-title").textContent=months>1?`${months}-month trend`:"Trend";
  if(!has){if(chartReg["c-trend"]){chartReg["c-trend"].destroy();delete chartReg["c-trend"];}box.innerHTML='<div class="loading">No trend data.</div>';return;}
  const th=chartTheme();
  // Straight segments, no area fill: smoothing invents values between months and
  // a fill implies a zero baseline the axis may not have (docs/ui-standard.md §6).
  mkChart("c-trend",{type:"line",
    data:{labels:trend.map(t=>fmtPeriod(t.period,"axis")),datasets:[
      {label:"Actual",data:trend.map(t=>t.value),borderColor:th.accent,borderWidth:2,tension:0,
        pointRadius:2,pointHoverRadius:4,pointBackgroundColor:th.accent,spanGaps:true,fill:false},
      {label:"Budget",data:trend.map(t=>t.budget),borderColor:th.mut,borderWidth:2,borderDash:[5,4],
        tension:0,pointRadius:0,pointHoverRadius:3,fill:false,spanGaps:true}]},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      plugins:{legend:{display:false},tooltip:tooltipCfg(unit)},
      scales:{y:{grid:{color:th.grid},ticks:{color:th.mut,maxTicksLimit:5,callback:v=>fmt(v,unit)},border:{display:false}},
        x:{grid:{display:false},ticks:{color:th.mut,maxRotation:0,autoSkip:true,maxTicksLimit:7},border:{color:th.grid}}}}});
  const y=chartReg["c-trend"]&&chartReg["c-trend"].scales&&chartReg["c-trend"].scales.y;
  if(note&&y&&y.min>0)note.textContent=`Axis starts at ${fmt(y.min,unit)}, not zero.`;
}
function renderBvaChart(bva,unit){
  ensureCanvas("c-bva");const box=$("box-c-bva");
  const drop=msg=>{if(chartReg["c-bva"]){chartReg["c-bva"].destroy();delete chartReg["c-bva"];}box.innerHTML=`<div class="loading">${msg}</div>`;};
  if(!bva||bva.actual==null){drop("No data.");return;}
  // Half a comparison reads as a broken chart — say what's missing instead.
  if(bva.budget==null){drop(`No budget loaded for this metric. Actual: <b class="nbi-num">${fmt(bva.actual,unit)}</b>. Attach one from Import → Budget.`);return;}
  const th=chartTheme();
  mkChart("c-bva",{type:"bar",
    data:{labels:[`Actual ${fmt(bva.actual,unit)}`,`Budget ${fmt(bva.budget,unit)}`],datasets:[{label:"",data:[bva.actual,bva.budget],
      backgroundColor:[th.accent,th.mut],borderRadius:5,borderSkipped:false,barThickness:22}]},
    options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:tooltipCfg(unit,{label:c=>` ${fmt(c.parsed.x,unit)}`})},
      scales:{x:{beginAtZero:true,grid:{color:th.grid},ticks:{color:th.mut,maxTicksLimit:5,callback:v=>fmt(v,unit)},border:{display:false}},
        y:{grid:{display:false},ticks:{color:th.ink,font:{weight:"600"}},border:{display:false}}}}});
}
function renderBridgeChart(bridge,total,unit){
  ensureCanvas("c-bridge");const box=$("box-c-bridge");
  const sec=$("bridge-sec"),hint=$("bridge-hint");
  if(!bridge||!bridge.length){
    if(chartReg["c-bridge"]){chartReg["c-bridge"].destroy();delete chartReg["c-bridge"];}
    if(sec)sec.style.display="none";if(hint)hint.style.display="";   // hide, don't look broken
    return;
  }
  if(sec)sec.style.display="";if(hint)hint.style.display="none";
  const th=chartTheme();
  let run=0;const steps=bridge.map(b=>{const s={label:b.component,range:[run,run+b.impact],val:b.impact};run+=b.impact;return s;});
  steps.push({label:"Total",range:[0,total],val:total,isTotal:true});
  mkChart("c-bridge",{type:"bar",
    data:{labels:steps.map(s=>s.label),datasets:[{label:"",data:steps.map(s=>s.range),
      backgroundColor:steps.map(s=>s.isTotal?th.ink:s.val>=0?th.green:th.red),
      borderRadius:4,borderSkipped:false}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:tooltipCfg(unit,{label:c=>` ${c.label}: ${fmt(steps[c.dataIndex].val,unit)}`})},
      scales:{y:{grid:{color:th.grid},ticks:{color:th.mut,maxTicksLimit:5,callback:v=>fmt(v,unit)},border:{display:false}},
        x:{grid:{display:false},ticks:{color:th.ink,font:{weight:"600"}},border:{color:th.grid}}}}});
}
function trendTable(trend,unit){if(!trend.length)return"";return`<details class="tbl"><summary>Show data table</summary><table><thead><tr><th>Period</th><th>Actual</th><th>Budget</th></tr></thead><tbody>${trend.map(t=>`<tr><td>${esc(fmtPeriod(t.period))}</td><td class="nbi-num">${fmt(t.value,unit)}</td><td class="nbi-num">${fmt(t.budget,unit)}</td></tr>`).join("")}</tbody></table></details>`;}

/* ---------- metric detail ---------- */
function metricHash(metric,period){return routeHash({tab:"kpis",period,metric});}
function openDetail(i,{fromRoute=false}={}){
  const f=shownFacts[i];if(!f||!f.has_data)return;detailFact=f;nav("detail");
  if(!fromRoute)pushRoute(metricHash(f.metric,currentPeriod));
  setDocTitle(`${f.metric} · ${fmtPeriod(currentPeriod)}`);
  // Phones show one pane at a time, so start the document at its top.
  if(!wideWorkspace.matches){$("main").scrollTop=0;window.scrollTo(0,0);}
  markQueueSelection();
  $("d-title").textContent=f.metric;const dg=f.direction_good==="down";
  $("d-sub").innerHTML=`<span class="cat-dot" style="${catStyle(f.category)}"></span>${esc(f.category||"")} · ${esc(fmtPeriod(currentPeriod,"long"))} · <span class="nbi-num">${fmt(f.value,f.unit)}</span> `
    +deltaChip(priorPeriodLabel(),f.deltas.mom_pct,dg,deltaText(f,"mom"))+" "+deltaChip("vs plan",f.deltas.budget_var_pct,dg,deltaText(f,"budget"))
    +` <span class="dir-note">${dg?"Lower is better":"Higher is better"}</span>`
    +(f.is_anomaly?' <span class="anom">Anomaly</span>':"");
  const cd=f.chart_data||{};
  const bva=cd.budget_vs_actual||{};
  $("d-figs").innerHTML=[["Actual",f.unavailable?"—":fmt(f.value,f.unit)],["Budget",bva.budget!=null?fmt(bva.budget,f.unit):"—"],
    ["Vs plan",deltaText(f,"budget")||"—"],[capFirst(priorPeriodLabel()),deltaText(f,"mom")||"—"],["Vs last year",deltaText(f,"yoy")||"—"]]
    .map(([k,val])=>`<div><dt>${k}</dt><dd class="nbi-num">${esc(val)}</dd></div>`).join("");
  $("d-regen").textContent=f.narrative?"Regenerate":"Draft commentary";
  // Restore canvases (an earlier "no data" state may have replaced them with text).
  $("d-trend").innerHTML=trendTable(cd.trend||[],f.unit);
  ensureCanvas("c-trend");ensureCanvas("c-bva");ensureCanvas("c-bridge");
  renderTrendChart(cd.trend||[],f.unit);
  $("d-bva-sub").textContent=f.deltas.budget_var_abs==null?""
    :isPctKpi(f)?`${deltaText(f,"budget")||"–"} vs budget`
    :`${fmt(f.deltas.budget_var_abs,f.unit)} vs budget (${pct(f.deltas.budget_var_pct)||"–"})`;
  renderBvaChart(cd.budget_vs_actual,f.unit);
  renderBridgeChart(cd.variance_bridge,f.deltas.budget_var_abs,f.unit);
  $("d-narr").className="narr"+(f.narrative?"":" muted");
  renderReviewStrip(f);
  {const rc=$("d-rootcause");if(rc){rc.style.display="none";rc.innerHTML="";}}
  $("d-grounding").style.display="none";groundingData=null;
  if(f.narrative&&f.report_id&&!f.edited){
    // Show the text at once; per-sentence grounding replaces it when it arrives.
    $("d-narr").textContent=f.narrative;
    renderGroundedNarrative(f.report_id);
  }else{
    $("d-narr").innerHTML=f.narrative?esc(f.narrative)+(f.edited&&f.original_narrative?`<details class="tbl" style="margin-top:8px"><summary>Show original (pre-edit)</summary><p style="margin-top:6px;color:var(--mut)">${esc(f.original_narrative)}</p></details>`:""):"No commentary yet. Draft it with the button at the top of this close.";
  }
  $("d-narr-meta").textContent=narrMeta(f);
  $("d-chips").innerHTML=(f.sources||[]).map(s=>`<span class="chip" role="button" tabindex="0" title="Open source note" data-act="source" data-arg="${esc(s.id)}">${DOCICON}${esc(s.title||s.id)}</span>`).join("");
  renderWhyNone(f);
  $("d-qa-answer").innerHTML="";$("d-qa").value="";qaThread=[];   // fresh conversation per metric
  loadCorrelations(f.metric);
}
/* ---------- narrative drill-down (v3.0) ---------- */
let groundingData=null;
const G_FIELD={
  "current value":"current value","prior-period value":"prior-period value",
  "budget variance (abs)":"budget variance","month-over-month %":"month-over-month",
  "year-over-year %":"year-over-year","budget variance %":"vs plan"};
async function renderGroundedNarrative(reportId){
  const el=$("d-narr");
  try{groundingData=await api(`/reports/${reportId}/grounding`);}
  catch{el.textContent=detailFact?detailFact.narrative||"":"";return;}
  const sents=(groundingData.sentences||[]);
  if(!sents.length){el.textContent=(detailFact&&detailFact.narrative)||"";return;}
  el.innerHTML=sents.map(s=>`<span class="gsent ${s.kind}" data-i="${s.idx}" role="button" tabindex="0" title="Show what supports this sentence" onclick="showSentenceGrounding(${s.idx})">${esc(s.text)}</span>`).join(" ")
    +`<div class="g-legend">
        <span><i style="border-color:var(--green2)"></i>traces to a figure</span>
        <span><i style="border-color:var(--accent)"></i>cites a source</span>
        <span><i style="border-color:var(--amber);border-top-style:dashed"></i>unverified number</span>
        <span style="color:var(--mut2)">Click any sentence →</span>
      </div>`;
}
function gFmtVal(v,isPct){return isPct?((v>0?"+":"")+v+"%"):fmt(v,"USD");}
function showSentenceGrounding(idx){
  if(!groundingData)return;
  const s=groundingData.sentences[idx];if(!s)return;
  document.querySelectorAll("#d-narr .gsent").forEach(e=>e.classList.toggle("sel",+e.dataset.i===idx));
  const box=$("d-grounding");let html="";
  const gCheck='<svg viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="width:12px;height:12px;flex:none"><path d="M20 6 9 17l-5-5"/></svg>';
  if(s.facts&&s.facts.length){
    html+=`<div class="g-lbl">Grounded in computed facts</div>`
      +s.facts.map(f=>`<div class="g-fact">${gCheck}<span>${esc(G_FIELD[f.field]||f.field)}</span> = <b>${gFmtVal(f.value,f.is_percent)}</b></div>`).join("");
  }
  if(s.context_ids&&s.context_ids.length){
    const chips=s.context_ids.map(id=>`<span class="chip" role="button" tabindex="0" data-act="source" data-arg="${esc(id)}">${DOCICON}${esc(id)}</span>`).join("");
    html+=`<div class="g-lbl" style="margin-top:${s.facts&&s.facts.length?"11px":"0"}">Cited context</div><div class="chips">${chips}</div>`;
  }
  if(s.kind==="numeric-unverified")
    html+=`<div class="g-warn" style="margin-top:8px">⚠ Contains a figure not traced to a computed fact — flagged for review, never hidden.</div>`;
  if(!html)html=`<div class="g-none">General statement — no specific figure or source grounds this sentence.</div>`;
  box.innerHTML=html;box.style.display="";
}
async function loadCorrelations(metric){
  const el=$("d-correlations");el.innerHTML="";
  try{
    const pairs=await api(`/correlations?metric=${encodeURIComponent(metric)}`);
    if(!pairs.length)return;
    el.innerHTML=`<div class="psub" style="margin:0 0 6px">Correlated metrics (historical)</div>`
      +`<div class="chips">`+pairs.slice(0,4).map(p=>{
        const pos=p.direction==="positive";
        return`<span class="chip" style="color:${pos?"var(--green)":"var(--red)"};background:${pos?"var(--green-bg)":"var(--red-bg)"};border-color:currentColor" title="Pearson r=${p.r}, ${p.months} months">${pos?"↑":"↓"} ${esc(p.metric_b)} (r=${p.r})</span>`;
      }).join("")+`</div>`;
  }catch{}
}
async function regenDetail(){
  if(!detailFact)return;
  await genOne(detailFact.metric,$("d-regen"));
  // openDetail indexes the FILTERED list (shownFacts), not currentFacts.
  const f=currentFacts.find(x=>x.metric===detailFact.metric);
  const i=f?shownFacts.indexOf(f):-1;
  if(i>=0)openDetail(i);
}
// Conversation memory (Phase 3): a per-metric thread of {question, answer} turns.
// openDetail resets it; askQA sends the recent turns so follow-ups ("why?",
// "vs last year?") resolve against the conversation.
let qaThread=[];
function renderQAThread(pending){
  const el=$("d-qa-answer");if(!el)return;
  if(!qaThread.length&&!pending){el.innerHTML="";return;}
  const turns=qaThread.map(t=>{
    const srcs=(t.sources||[]).map(s=>`<span class="chip" role="button" tabindex="0" data-act="source" data-arg="${esc(s.id)}">${DOCICON}${esc(s.title||s.id)}</span>`).join("");
    return `<div class="qa-turn">
      <div class="qa-q">${esc(t.question)}</div>
      <div class="qa-a"><div class="narr" style="font-size:15px">${esc(t.answer)}</div>
        ${(srcs||t.grounded)?`<div class="chips" style="margin-top:8px">${srcs}${t.grounded?seal(true):""}</div>`:""}</div>
    </div>`;
  }).join("");
  const wait=pending?`<div class="qa-turn"><div class="qa-q">${esc(pending)}</div>
    <div class="qa-a"><div class="loading" style="padding:6px 0"><span class="spin"></span> Thinking…</div></div></div>`:"";
  el.innerHTML=`<div class="qa-thread">${turns}${wait}</div>`;
  el.scrollTop=el.scrollHeight;
}
async function askQA(){
  const q=$("d-qa").value.trim();if(!q||!detailFact)return;
  if(detailFact.aggregated){$("d-qa-answer").innerHTML='<div class="banner info" style="margin:0">Follow-up Q&A works at the monthly level — switch the dashboard to Month, open this metric, and ask again.</div>';return;}
  $("d-qa").value="";
  renderQAThread(q);   // show the question immediately with a thinking indicator
  try{
    const history=qaThread.map(t=>({question:t.question,answer:t.answer}));
    const r=await api("/ask",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({metric:detailFact.metric,period:detailFact.period||currentPeriod,question:q,history})});
    qaThread.push({question:q,answer:r.answer||"No grounded answer available.",sources:r.sources||[],grounded:r.grounded});
    renderQAThread();
  }catch(e){
    renderQAThread();
    $("d-qa-answer").insertAdjacentHTML("beforeend",`<div class="banner err" style="margin:8px 0 0">${esc(e.message)}</div>`);
  }
}

/* ---------- generation & feedback ---------- */
async function genOne(metric,btn){
  const regen=btn&&/regenerat/i.test(btn.textContent),gp=currentPeriod,gd=activeDatasetId;
  generatingSet.add(metric);renderCards();
  try{
    const r=await api("/generate-insight",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({metric,period:gp,force:regen})});
    if(currentPeriod!==gp||activeDatasetId!==gd){generatingSet.delete(metric);return;}   // user moved on — drop result
    applyGenerated(r);generatingSet.delete(metric);renderCards();updateGenButton();
    toast(r.faithfulness==="failed"?`${metric}: numbers need review`:(r.cached?"Served from cache — no LLM cost":`Commentary drafted for ${metric}`));
  }catch(e){generatingSet.delete(metric);renderCards();toast("Failed: "+errMsg(e));}
}
// Rough per-narrative cost estimate (gpt-4o-class: ~1.5k prompt + ~180 completion tokens).
const EST_COST_PER_NARR=0.006;
let generatingSet=new Set(), skipRise=false;
// Simple concurrency pool: run `worker` over `items`, at most `n` in flight.
/* ---------- a big movement with no recorded reason asks for one, right where it's read ---------- */
// "Big" is the same rule the close checklist uses: an anomaly, or more than 10% against plan.
function needsReason(f){
  if(!f||!f.has_data||(f.sources&&f.sources.length))return false;
  const plan=f.deltas&&f.deltas.budget_var_pct;
  return !!f.is_anomaly||(plan!=null&&Math.abs(plan)>10);
}
function renderWhyNone(f){
  const el=$("d-why");if(!el)return;
  if(!needsReason(f)){el.innerHTML="";return;}
  el.innerHTML=`<div class="why-none" role="note"><span><b>No reason recorded.</b> ${canReview()
    ?"Add a context note and the commentary will cite it next time it's drafted."
    :"An analyst can add the reason as a context note."}</span>${canReview()?`<button class="btn sm" data-act="reason" data-arg="${esc(f.metric)}">Add a reason</button>`:""}</div>`;
}
// Opens the close's Context tab with a new note already pointed at this KPI and month.
function addReasonFor(metric){
  nav("context");
  setTimeout(()=>{
    ctxNew();
    $("c-type").value="event_note";
    if(!$("c-title").value)$("c-title").value=`Why ${metric} moved in ${fmtPeriod(currentPeriod,"long")}`;
    $("c-tags").value=metric;
    if(granularity==="month")$("c-date").value=currentPeriod;
    $("c-body").focus();
  },60);
}

/* ---------- bulk approve: only drafts a person doesn't need to read one by one ---------- */
// A draft qualifies when every figure passed the check and nothing about the KPI is unusual.
// Anomalies and flagged drafts stay out on purpose: those are the ones worth reading.
function bulkApprovable(facts){
  return (facts||[]).filter(f=>f.has_data&&f.report_id&&f.narrative&&f.faithfulness==="passed"
    &&!f.is_anomaly&&f.review_status!=="approved");
}
function renderBulkApprove(){
  const el=$("q-bulk");if(!el)return;
  const ok=granularity==="month"?bulkApprovable(currentFacts):[];
  const held=(currentFacts||[]).filter(f=>f.has_data&&f.narrative&&f.review_status!=="approved").length-ok.length;
  el.innerHTML=ok.length>1?`<button class="btn" onclick="bulkApprove()">Approve ${plural(ok.length,"checked draft","checked drafts")}</button>
    <small>Every figure in these passed the check.${held?` ${plural(held,"anomaly or flagged draft stays","anomalies and flagged drafts stay")} for you to read.`:""}</small>`:"";
}
async function bulkApprove(){
  const ok=bulkApprovable(currentFacts);if(!ok.length)return;
  const names=ok.slice(0,5).map(f=>f.metric).join(", ")+(ok.length>5?` and ${ok.length-5} more`:"");
  if(!await modalConfirm(`Approve ${plural(ok.length,"draft")}?`,`${names}. Every figure in these drafts passed the check against the computed facts. You can still edit or request changes on any of them later.`,{okLabel:"Approve",danger:false}))return;
  let failed=0;const approved=[];
  await runPool(ok,4,async f=>{
    const before=f.review_status||null;
    try{await api(`/reports/${f.report_id}/review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:"approved"})});
      f.review_status="approved";approved.push({f,before});}
    catch{failed++;}
  });
  afterBulkReview();
  const msg=failed?`Approved ${approved.length}; ${plural(failed,"draft")} failed, try again`:`Approved ${plural(approved.length,"draft")}`;
  if(approved.length)toastAction(msg,"Undo",()=>undoBulkApprove(approved));else toast(msg);
}
function afterBulkReview(){overviewFor=null;renderCards();renderMonthHead();loadCloseMeta();refreshReviewBadge();}
// Put each draft back to the review state it had before the bulk approval.
async function undoBulkApprove(approved){
  let failed=0;
  await runPool(approved,4,async({f,before})=>{
    try{await api(`/reports/${f.report_id}/review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:before||"draft"})});
      f.review_status=before;}
    catch{failed++;}
  });
  afterBulkReview();
  toast(failed?`Undid ${approved.length-failed}; ${plural(failed,"approval")} couldn't be undone`:`Undid ${plural(approved.length,"approval")}`);
}

async function runPool(items,n,worker){
  let i=0;
  await Promise.all(Array(Math.min(n,items.length)).fill(0).map(async()=>{
    while(i<items.length){const it=items[i++];await worker(it);}
  }));
}
function ungeneratedFacts(){return currentFacts.filter(f=>f.has_data&&!f.narrative&&!f.aggregated);}
function updateGenButton(){
  const btn=$("gen-all");if(!btn||btn.disabled)return;
  if(granularity!=="month"){btn.style.display="none";return;}
  btn.style.display="";
  const n=ungeneratedFacts().length;
  if(!n){btn.innerHTML=`Redraft all<small>commentary is up to date</small>`;return;}
  btn.innerHTML=`Draft ${plural(n,"commentary","commentaries")}<small>≈ $${(n*EST_COST_PER_NARR).toFixed(2)} · ~${n*7}s</small>`;
}
function applyGenerated(r){
  const f=currentFacts.find(x=>x.metric===r.metric);
  if(f)Object.assign(f,{narrative:r.narrative,sources:r.sources||[],confidence:r.confidence,
    faithfulness:r.faithfulness,report_id:r.report_id,cost_usd:r.cost_usd,
    trend_streak:r.trend_streak||f.trend_streak,generated_by:r.generated_by});
}
async function generateAll(){
  if(currentUser&&currentUser.role==="viewer")return;
  const targets=(granularity==="month"?ungeneratedFacts():[]);
  const all=currentFacts.filter(f=>f.has_data&&!f.aggregated);
  const list=targets.length?targets:all;   // if all done, regenerate all
  if(!list.length){toast("Nothing to generate at this granularity");return;}
  const btn=$("gen-all");btn.disabled=true;
  const gp=currentPeriod,gg=granularity,gd=activeDatasetId,force=!targets.length,total=list.length;
  generatingSet=new Set(list.map(f=>f.metric));skipRise=true;renderCards();
  {const b=$("import-banner");if(b&&b.classList.contains("info"))b.style.display="none";}
  let done=0,errors=0,stopMsg=null;
  await runPool(list,3,async(f)=>{
    if(stopMsg){generatingSet.delete(f.metric);return;}   // an allowance or budget limit was hit: don't keep trying
    try{
      const r=await api("/generate-insight",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({metric:f.metric,period:gp,force})});
      if(currentPeriod!==gp||granularity!==gg||activeDatasetId!==gd)return;   // user moved on — drop the result
      applyGenerated(r);
    }catch(e){errors++;if(e.status===429||e.status===402)stopMsg=e.message;}   // leave the card un-drafted; user can retry
    if(currentPeriod!==gp||granularity!==gg||activeDatasetId!==gd)return;
    generatingSet.delete(f.metric);done++;
    btn.innerHTML=`<span class="spin"></span> ${done} of ${total}`;
    renderCards();
  });
  skipRise=false;btn.disabled=false;generatingSet.clear();updateGenButton();
  if(currentPeriod!==gp)return;
  // Head, tiles, banner and cards must agree once generation finishes (QA #8).
  setFreshness();maybePromptGenerate();renderCards();
  if(stopMsg){toast(stopMsg);return;}
  if(errors){toast(`${plural(errors,"KPI wasn't","KPIs weren't")} drafted. Try again in a moment.`);return;}
  const failed=currentFacts.filter(f=>f.faithfulness==="failed").length;
  toast(failed?`Done: ${plural(failed,"draft needs","drafts need")} review`:"All commentary drafted");
}
async function feedback(id,action,btn,extra){
  try{
    await api("/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({report_id:id,action,...extra})});
    if(btn)btn.classList.add(action==="accepted"?"on":"on-bad");
    toast(action==="accepted"?"Accepted":action==="rejected"?"Rejected":"Edit saved");
  }catch(e){toast("Couldn't save feedback: "+e.message);}
}
async function rejectNarr(id,btn){
  const r=await modalInput("Reject commentary",{label:"Why is this wrong? (optional)",textarea:true,placeholder:"e.g. attributes the drop to the wrong cause",okLabel:"Reject"});
  if(r===null)return;   // cancelled
  feedback(id,"rejected",btn,{reason:r||null});
}
async function editNarr(id){
  const f=currentFacts.find(x=>x.report_id===id);
  const cur=(f&&f.narrative)||"";
  const t=await modalInput(`Edit commentary${f?": "+f.metric:""}`,{label:"Narrative",value:cur,textarea:true,okLabel:"Save"});
  if(t==null||t===cur)return;
  await feedback(id,"edited",null,{edited_text:t});loadFacts();
}

/* ---------- digest ---------- */
let digestGenerating=false;
let digestRevision=0;
let digestNow=null;   // this close's digest as leadership would receive it
// The digest reads as one document: a title, then each mover as a numbered section.
function renderDigest(d){
  const items=(d&&d.items)||[];
  const judged=items.filter(it=>it.faithfulness).length,checked=items.filter(it=>it.faithfulness==="passed").length;
  const sentAt=d&&(d.sent_at||d.created_at);
  const meta=[d&&d.total_kpis?`${d.approved_kpis} of ${plural(d.total_kpis,"KPI","KPIs")} approved`:null,
    sentAt?`Sent ${esc(fmtDate(sentAt))}`:"Not sent yet",
    judged?`${checked} of ${judged} figure checks passed`:null].filter(Boolean).join(" · ");
  const heading=`<header class="dg-head"><p class="dg-kick">Executive digest · ${esc(fmtPeriod(d.period))}</p>
    <h2>${esc(fmtPeriod(d.period,"long"))}: the largest movements against plan</h2><p class="dg-meta">${meta}</p></header>`;
  return heading+(items.map((it,i)=>{
    const v=it.budget_var_abs,f={unit:it.unit,deltas:{budget_var_abs:it.budget_var_abs,budget_var_pct:it.budget_var_pct}};
    const good=v==null||v===0?null:(it.direction_good==="down"?v<0:v>0);
    return `<section class="dg-sec"><span class="dg-n nbi-num" aria-hidden="true">${esc(i+1)}</span><div>
      <p class="dg-metric">${esc(it.metric)}</p><h3>${esc(it.headline)}</h3>
      ${it.detail?`<p class="dg-body">${esc(it.detail)}</p>`:""}
      <div class="dg-figs">
        ${v!=null?`<span class="nbi-num ${good===null?"":good?"c-ok":"c-exc"}">${isPctKpi(f)?esc(deltaText(f,"budget")||"–")+" vs plan"
          :`${esc(fmt(v,it.unit||"USD"))} vs plan (${esc(pct(it.budget_var_pct)||"–")})`}</span>`:""}
        ${it.faithfulness==="passed"?seal(true):it.faithfulness==="failed"?seal(false):""}
      </div></div></section>`;}).join(""));
}
// Where the close's digest stands: sent, ready to send, or waiting on approvals.
function digestStateBar(d){
  if(d.sent_at)return `<div class="dg-state ready" role="status"><b>Sent ${esc(fmtDate(d.sent_at))}.</b> Leadership received the approved commentary below.</div>`;
  if(d.ready)return `<div class="dg-state ready" role="status"><b>Every KPI is approved.</b> Sending delivers this digest to the channels set up in Settings.</div>`;
  const n=(d.pending||[]).length,names=(d.pending||[]).slice(0,3).join(", ")+(n>3?` and ${n-3} more`:"");
  return `<div class="dg-state wait" role="status"><span><b>${plural(n,"KPI still needs","KPIs still need")} approval</b> before this can be sent: ${esc(names)}.</span>
    <button class="btn sm" onclick="nav('kpis')">Open KPIs</button></div>`;
}
async function loadDigest(){return loadDigestHistory();}
// In a close, the digest tab shows that close's digest; earlier sent ones sit beside it.
async function loadDigestHistory(){
  const revision=++digestRevision;
  const done=screenLoading("digest-list","Loading the digest…");
  $("digest-history").innerHTML="";$("digest-status").innerHTML="";$("digest-generate").style.display="none";
  try{
    const monthly=granularity==="month"&&currentPeriod;
    const [d,runs]=await Promise.all([
      monthly?api(`/digest?period=${encodeURIComponent(currentPeriod)}&top_n=5`):Promise.resolve(null),
      api("/digests/history")]);
    if(!done()||revision!==digestRevision)return;
    digestNow=d&&d.items?d:null;
    if(!monthly){
      $("digest-list").innerHTML=screenState("Digests are prepared per month","Switch the closes list to months to read or send a month's digest.","setGranularity('month')","Show months");
    }else if(!digestNow||!digestNow.items.length){
      const n=digestNow?digestNow.total_kpis:0,a=digestNow?digestNow.approved_kpis:0;
      $("digest-list").innerHTML=screenState(`Nothing approved yet for ${fmtPeriod(currentPeriod,"long")}`,
        n?`The digest is built from approved commentary only. ${a} of ${plural(n,"KPI","KPIs")} are approved so far.`
         :"The digest is built from approved commentary only. Import data and approve drafts to build it.",
        "nav('kpis')","Open KPIs");
    }else{
      $("digest-list").innerHTML=digestStateBar(digestNow)+renderDigest(digestNow);
      $("digest-generate").style.display=digestNow.ready&&!digestNow.sent_at?"":"none";
    }
    const past=(Array.isArray(runs)?runs:[]).filter(r=>r.period!==currentPeriod);
    $("digest-history").innerHTML=past.length?'<h2 class="digest-history-title">Earlier digests</h2>'+past.map(r=>`<button class="dg-past" data-act="close" data-arg="${esc(r.period)}">
      <b>${esc(fmtPeriod(r.period))}</b><small>Sent ${esc(fmtDate(r.created_at))}${r.trigger==="scheduled"?" on schedule":""}</small>
      <small>${esc((r.items||[]).slice(0,3).map(it=>it.metric).join(", "))}</small></button>`).join(""):"";
  }catch(e){if(done()&&revision===digestRevision)$("digest-list").innerHTML=screenError(e,"loadDigest()");}
}
// Sending is the one step that reaches leadership; the server refuses it until every KPI is approved.
async function sendDigest(){
  if(!canReview()||digestGenerating||!digestNow||!digestNow.ready)return;
  const n=digestNow.items.length;
  if(!await modalConfirm(`Send the ${fmtPeriod(currentPeriod,"long")} digest?`,
    `${plural(n,"item","items")} of approved commentary go to every channel set up in Settings (Slack, email, webhook).`,
    {okLabel:"Send digest",danger:false}))return;
  digestGenerating=true;const btn=$("digest-generate");btn.disabled=true;
  try{
    await api(`/digest/send?period=${encodeURIComponent(currentPeriod)}&top_n=5`,{method:"POST"});
    toast("Digest sent");closeShared.add(currentPeriod);
    await loadDigestHistory();loadCloseMeta();
  }catch(e){toast("Couldn't send the digest: "+errMsg(e));}
  finally{digestGenerating=false;btn.disabled=false;}
}


/* ---------- context ---------- */
async function loadContext(){
  const done=screenLoading("ctx-rows","Loading the context library…");
  let docs;try{docs=await api("/context");}catch(e){if(done())$("ctx-rows").innerHTML=screenError(e,"loadContext()");return;}
  if(!done())return;
  ctxDocs=docs;
  if(!docs.length){
    $("ctx-scope").innerHTML="";$("ctx-view").style.display="none";
    $("ctx-rows").innerHTML=currentUser&&["executive","viewer"].includes(currentUser.role)
      ?screenState("No context notes yet","Business events, definitions and policies help explain metric movements. Ask an analyst to add a note.","nav('kpis')","Open KPIs")
      :screenState("No context notes yet","Add business events, definitions and policies to ground the explanations of your metrics.","$('c-title').focus()","Add a context note","empty",{writeOnly:true});
    return;
  }
  renderContextList();
}
// A close's notes: dated in its period, or undated (definitions and policies apply to every close).
function ctxInClose(d){return !d.effective_date||!currentPeriod||String(d.effective_date).startsWith(currentPeriod.slice(0,granularity==="year"?4:7));}
const ctxWhen=(d,style)=>d.effective_date?(/^\d{4}-\d{2}$/.test(d.effective_date)?fmtPeriod(d.effective_date,style):fmtDate(d.effective_date)):"Every close";
let ctxScope="all",ctxDocs=[],ctxSel=null,ctxComposing=false;
function renderContextList(){
  const scoped=ctxScope==="close"&&!!currentPeriod;
  $("ctx-scope").innerHTML=currentPeriod&&ctxScope!=="library"
    ?`<button aria-pressed="${scoped}" onclick="ctxScope='close';renderContextList()">This close · ${ctxDocs.filter(ctxInClose).length}</button><button aria-pressed="${!scoped}" onclick="ctxScope='all';renderContextList()">All notes · ${ctxDocs.length}</button>`:"";
  const docs=scoped?ctxDocs.filter(ctxInClose):ctxDocs;
  if(ctxSel!=null&&!docs.some(d=>d.id===ctxSel))ctxSel=null;
  if(ctxSel==null&&docs.length&&!ctxComposing)ctxSel=docs[0].id;
  $("ctx-rows").innerHTML=docs.map(d=>{
    const on=d.id===ctxSel;
    return `<div class="ctx-item${on?" on":""}">
      <button class="ctx-open" onclick="ctxOpen(${esc(d.id)})"${on?' aria-current="true"':""}><b>${esc(d.title)}</b><small>${esc(d.type.replace("_"," "))} · ${esc(ctxWhen(d))}</small></button>
      <button class="row-del write-only" aria-label="Delete ${esc(d.title)}" onclick="delContext(${esc(d.id)})">${XICON}</button></div>`;
  }).join("")||`<p class="ctx-none">No notes dated in ${esc(fmtPeriod(currentPeriod))}. <button class="linkbtn" onclick="ctxScope='all';renderContextList()">Show all notes</button></p>`;
  paintContextDetail();
}
function paintContextDetail(){
  const d=ctxDocs.find(x=>x.id===ctxSel),view=$("ctx-view"),form=$("ctx-form");
  form.style.display=!d&&canReview()?"":"none";
  if(!d){view.style.display="none";return;}
  view.style.display="";
  view.innerHTML=`<span class="ctx-type">${esc(d.type.replace("_"," "))}</span><h2>${esc(d.title)}</h2><p class="ctx-body">${esc(d.body)}</p>
    <dl><dt>Feeds</dt><dd>${d.metric_tags.length?d.metric_tags.map(t=>`<span class="ctx-tag">${esc(t)}</span>`).join(" "):"Every KPI"}</dd>
    <dt>Applies to</dt><dd>${esc(ctxWhen(d,"long"))}</dd></dl>`;
}
function ctxOpen(id){ctxSel=id;ctxComposing=false;renderContextList();}
function ctxNew(){
  ctxSel=null;ctxComposing=true;renderContextList();$("ctx-form").style.display="";$("c-title").focus();
  if(currentPeriod&&granularity==="month"&&!$("c-date").value)$("c-date").value=currentPeriod;
}
async function addContext(e){
  e.preventDefault();
  const tags=$("c-tags").value.split(",").map(s=>s.trim()).filter(Boolean);
  await api("/context",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({type:$("c-type").value,title:$("c-title").value,body:$("c-body").value,metric_tags:tags,effective_date:$("c-date").value||null})});
  toast("Added to the context library");["c-title","c-body","c-tags","c-date"].forEach(id=>$(id).value="");ctxComposing=false;ctxSel=null;loadContext();
}
async function delContext(id){
  if(!await modalConfirm("Delete context note","Delete this context note? Commentary will no longer cite it.",{okLabel:"Delete"}))return;
  try{await api(`/context/${id}`,{method:"DELETE"});toast("Deleted");loadContext();}catch(e){toast(e.message);}
}

/* ---------- import ---------- */
let upload=null,profile=null,lastImport=null,importMode="new";
let importStep=1,kpiLoading=false,kpiSaving=false,importLeaving=false;
// The dataset that was open when the current import started. Steps 1-2 save
// nothing, so an import started elsewhere is cleared rather than shown in
// another dataset's Data tab.
let importStartedFor;
function dropStaleImport(){
  if(importStep!==1&&!hasUnfinishedImport()&&importStartedFor!==activeDatasetId)resetImport();
}
function setStep(n){importStep=n;for(let i=1;i<=3;i++){const el=$("st-"+i),done=i<n;
    el.className="step"+(done?" done":i===n?" active":"");
    if(i===n)el.setAttribute("aria-current","step");else el.removeAttribute("aria-current");
    el.innerHTML=(done?'<svg class="step-tick" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7"/></svg><span class="visually-hidden">done · </span>':esc(i)+" · ")+esc(["Upload","Map columns","Pick KPIs"][i-1]);}
  $("imp-upload").style.display=n===1?"":"none";$("imp-map").style.display=n===2?"":"none";$("imp-kpis").style.display=n===3?"":"none";
  if(n!==2)$("imp-join-result").style.display="none";}
function hasUnfinishedImport(){return importStep===3&&lastImport?.datasetId!=null;}
async function leaveImport(){
  if(kpiSaving||importLeaving)return false;
  if(!hasUnfinishedImport())return true;
  importLeaving=true;updateKpiSelection();
  try{
    closeSidebar();
    const answer=modalConfirm("Leave import?","This dataset is unfinished. Delete it and leave, or cancel to keep choosing your KPIs.",{okLabel:"Delete and leave"});
    $("modal-ok").focus?.();
    if(!await answer)return false;
    await api(`/datasets/${lastImport.datasetId}`,{method:"DELETE"});
    resetImport();
    try{await loadPeriods();}catch(e){$("import-banner").style.display="";$("import-banner").className="banner err";$("import-banner").innerHTML=`Unfinished import deleted, but the dataset list could not refresh: ${esc(e.message)} <button class="btn sm" onclick="loadPeriods()">Retry</button>`;}
    toast("Unfinished import deleted");return true;
  }catch(e){$("imp-result").innerHTML=`<div class="banner err">${esc(e.message)}. Your import is still open; try leaving again to retry.</div>`;return false;}
  finally{importLeaving=false;updateKpiSelection();if(hasUnfinishedImport())$("imp-name").focus?.();}
}
async function requestImportAction(action){if(await leaveImport())return action();}
function requestImportView(view){if(["import","data","datahub"].includes(view)||(!hasUnfinishedImport()&&!kpiSaving))return nav(view);return requestImportAction(()=>nav(view));}
async function importRouteChanged(){
  if(!routerReady)return;
  if(!hasUnfinishedImport())return route();
  const target=location.hash,here=routeHash(currentPlace.place?currentPlace:{tab:currentPlace.tab,period:currentPeriod});
  if(target===here)return;
  history.pushState(null,"",here);
  if(await leaveImport()){history.replaceState(null,"",target);route();}
}
function dropFile(e){e.preventDefault();$("drop").classList.remove("drag");const f=e.dataTransfer.files[0];if(f)uploadFile(f);}
function resetImport(){
  // Lets the user land back on step 1 and pick a DIFFERENT file. A bare file
  // input never re-fires 'change' for the same filename, and stale upload/
  // profile state would otherwise carry into the next attempt.
  upload=null;profile=null;lastImport=null;kpiLoading=false;
  const input=$("file-in");if(input)input.value="";
  const month=$("map-month");if(month)month.value="";
  $("imp-result").innerHTML="";$("map-warnings").style.display="none";$("map-hint").style.display="none";
  $("sheet-pick").style.display="none";$("tpl-banner").style.display="none";
  $("imp-join-result").style.display="none";
  setStep(1);
}
async function onImportModeChange(){
  importMode=$("imp-mode").value;
  $("imp-target-wrap").style.display=importMode==="join"?"":"none";
  $("imp-suffix-wrap").style.display=importMode==="join"?"":"none";
  $("imp-confirm-btn").textContent=importMode==="join"?"Match & attach budget":"Confirm columns";
  if(importMode==="join"){
    try{
      const d=await api("/datasets");
      $("imp-target").innerHTML=(d.datasets||[]).map(x=>`<option value="${esc(x.id)}">${esc(datasetDisplayName(x.name))} · ${esc(datasetCaption(x))}</option>`).join("")
        ||'<option value="">No datasets yet — import actuals first</option>';
    }catch{}
  }
}
// "Try it with sample data": the repo's synthetic export, run through the real import steps.
async function importSample(btn){
  if(btn)btn.disabled=true;
  try{
    const r=await fetch("/samples/fpa.csv");if(!r.ok)throw new Error(r.statusText||"Sample unavailable");
    const file=new File([await r.text()],"Sample FP&A export.csv",{type:"text/csv"});
    nav("datahub");await uploadFile(file);
  }catch(e){toast("Couldn't load the sample: "+errMsg(e));}
  finally{if(btn)btn.disabled=false;}
}
async function uploadFile(file){
  const fd=new FormData();fd.append("file",file);
  $("imp-result").innerHTML='<div class="loading"><span class="spin"></span> Uploading &amp; profiling…</div>';
  try{
    upload=await api("/ingest/upload",{method:"POST",body:fd});$("imp-result").innerHTML="";importStartedFor=activeDatasetId;
    if(upload)upload.filename=file.name;   // for the dataset-name default
    if(upload.sheets&&upload.sheets.length>1){$("sheet-pick").style.display="";$("sheet-sel").innerHTML=upload.sheets.map(s=>`<option>${esc(s)}</option>`).join("");}
    else $("sheet-pick").style.display="none";
    if(upload.suggested_template_id){$("tpl-banner").style.display="";$("tpl-banner").innerHTML=`Recognized format — <button class="btn sm" style="margin-left:8px" onclick="applyTpl(${upload.suggested_template_id})">Apply saved mapping</button>`;}
    else $("tpl-banner").style.display="none";
    setStep(2);await loadSchema();
  }catch(e){$("imp-result").innerHTML=`<div class="banner err">${warnIcon()}${esc(e.message)}</div>`;}
}
async function loadSchema(){
  const sheet=$("sheet-pick").style.display!=="none"?$("sheet-sel").value:null;
  profile=await api(`/ingest/${upload.upload_id}/schema${sheet?`?sheet=${encodeURIComponent(sheet)}`:""}`);
  $("layout-sel").value=profile.layout_guess;
  const wb=$("map-warnings");
  if(profile.mapping_warnings&&profile.mapping_warnings.length){
    wb.style.display="";
    wb.innerHTML=`<b>Worth checking before you confirm:</b><ul style="margin:6px 0 0 18px">${profile.mapping_warnings.map(w=>`<li>${esc(w)}</li>`).join("")}</ul>`;
  }else{wb.style.display="none";}
  renderMap();
}
// Initial role for a profiled column. A measure the profiler found to be the
// same figure as another (amount_local vs amount_usd) starts as "ignore" —
// the mapping warning already says only one is kept, and keeping both
// doubles every metric and sums mixed currencies.
function roleFor(c){
  return c.redundant_with&&c.guessed_role==="measure"?"ignore":c.guessed_role;
}
const ROLES=["period","metric_label","measure","budget","id","quantity","price","dimension","ignore"];
// What each role means to someone closing the books; the internal names stay in the API.
const ROLE_LABELS={period:"Month or date",metric_label:"KPI name",measure:"Actual amount",budget:"Budget or plan",
  id:"Row ID (not imported)",quantity:"Units sold",price:"Unit price",dimension:"Breakdown (region, product…)",ignore:"Don't import"};
function renderMap(){
  if(!profile)return;const layout=$("layout-sel").value;
  $("map-holder").innerHTML=`<table class="map"><thead><tr><th>Column</th><th>Type</th><th>Sample</th><th>What it holds</th></tr></thead><tbody>`+
    profile.columns.map(c=>{const wp=profile.wide_period_cols.includes(c.column_name);
      const role=roleFor(c);
      return`<tr><td><b>${esc(c.column_name)}</b>${layout==="wide"&&wp?' <span class="suggest">a month</span>':""}${c.redundant_with&&role==="ignore"?` <span class="suggest">duplicate of ${esc(c.redundant_with)}</span>`:""}</td>
        <td>${esc(c.dtype)}</td><td class="samp">${c.sample_values.slice(0,3).map(esc).join(" · ")}</td>
        <td>${layout==="wide"&&wp?'<span class="samp">One month of values</span>':`<select data-col="${esc(c.column_name)}" class="role" aria-label="What column ${esc(c.column_name)} holds" onchange="updateMapHint()">${ROLES.map(r=>`<option value="${r}" ${role===r?"selected":""}>${ROLE_LABELS[r]||r}</option>`).join("")}</select>`}</td></tr>`;
    }).join("")+`</tbody></table>`;
  updateMapHint();
}
// The month a dateless file covers: from the sheet itself (an as-of date), or
// picked here when no column holds each row's month.
function sheetPeriod(){return (profile&&profile.suggested_mapping&&profile.suggested_mapping.period_literal)||null;}
function needsMonthPick(){
  return $("layout-sel").value==="long"&&!sheetPeriod()&&![...document.querySelectorAll(".role")].some(s=>s.value==="period");
}
// What's still missing before these columns can be imported, said plainly; null when ready.
function mappingProblem(){
  if($("layout-sel").value!=="long")return null;
  const roles=[...document.querySelectorAll(".role")].map(s=>s.value);
  if(needsMonthPick()&&!$("map-month").value)return "This file has no date column. Set one column to “Month or date”, or choose the month the whole file covers.";
  if(!roles.includes("measure"))return "Set the column with the amounts to track to “Actual amount”.";
  return null;
}
function updateMapHint(){
  const wrap=$("map-month-wrap");if(wrap)wrap.hidden=!needsMonthPick();
  const el=$("map-hint");if(!el||$("layout-sel").value!=="long")return el&&(el.style.display="none");
  const measures=[...document.querySelectorAll(".role")].filter(s=>s.value==="measure").map(s=>s.dataset.col);
  const metricCol=[...document.querySelectorAll(".role")].find(s=>s.value==="metric_label");
  const periodLiteral=sheetPeriod();
  const parts=[];
  if(needsMonthPick())parts.push($("map-month").value
    ?`<b>Every row will be filed under ${esc(fmtPeriod($("map-month").value,"long"))}</b>, since no column holds a date.`
    :`<b>No column holds a date.</b> If the whole file covers one month, choose it under “Month these figures are for”. Otherwise set the right column to “Month or date”.`);
  if(periodLiteral)parts.push(`<b>Period ${esc(periodLiteral)} comes from this sheet</b> — it reports one as-of date, so every row is filed under it and no period column is needed.`);
  if(measures.length>1){
    const names=metricCol?`"${metricCol.dataset.col} ${measures[0]}"`:`"${measures[0]}"`;
    parts.push(`<b>${measures.length} amount columns selected</b> — each becomes its own metric${metricCol?`, crossed with <b>${esc(metricCol.dataset.col)}</b>`:""} (e.g. ${esc(names)}, …) instead of only the first being kept.`);
  }
  if(parts.length){el.style.display="";el.innerHTML=parts.join("<br>");}
  else{el.style.display="none";}
}
function buildMapping(){
  const layout=$("layout-sel").value,roleOf={};
  document.querySelectorAll(".role").forEach(s=>roleOf[s.dataset.col]=s.value);
  const by=r=>Object.keys(roleOf).filter(c=>roleOf[c]===r);
  if(layout==="wide")return{layout:"wide",wide_period_cols:profile.wide_period_cols,wide_metric_col:by("metric_label")[0]||null,wide_value_label:by("metric_label").length?null:"Value",id_col:by("id")[0]||null,dimension_cols:by("dimension")};
  const measures=by("measure");
  const periodLiteral=sheetPeriod()||(needsMonthPick()&&$("map-month").value)||null;
  const base={layout:"long",period_col:periodLiteral?null:(by("period")[0]||null),period_literal:periodLiteral,metric_col:by("metric_label")[0]||null,budget_col:by("budget")[0]||null,id_col:by("id")[0]||null,dimension_cols:by("dimension")};
  if(measures.length>1)return{...base,value_cols:measures};
  return{...base,value_col:measures[0]||null,quantity_col:by("quantity")[0]||null,price_col:by("price")[0]||null};
}
async function confirmMapping(m){
  if(importMode==="join")return confirmJoinBudget();
  const problem=m?null:mappingProblem();
  if(problem){$("imp-result").innerHTML=`<div class="banner err">${warnIcon()}${esc(problem)}</div>`;updateMapHint();return;}
  const mapping=m||buildMapping();
  $("imp-result").innerHTML='<div class="loading"><span class="spin"></span> Normalizing &amp; computing…</div>';
  try{
    const res=await api(`/ingest/${upload.upload_id}/mapping?save_template=${$("save-tpl").checked}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(mapping)});
    $("imp-result").innerHTML="";
    $("norm-summary").innerHTML=`Read ${plural(res.rows_normalized,"row")}: ${plural(res.metrics.length,"KPI","KPIs")} across ${plural(res.periods.length,"period")}.${res.template_id?" The column mapping is saved for next month's file.":""}`;
    // dataset_id is the dataset THIS import created; activeDatasetId is still
    // whatever was selected before (or null) until loadDatasets() runs.
    lastImport={rows:res.rows_normalized,metrics:res.metrics.length,periods:res.periods.length,datasetId:res.dataset_id??null};
    // The import made the new dataset active on the server; point this tab at it too,
    // so picking KPIs (POST /kpis) configures the new dataset, not the previous one.
    if(res.dataset_id!=null){activeDatasetId=res.dataset_id;importStartedFor=res.dataset_id;}
    // Default the dataset name to the uploaded filename without its extension.
    const fn=(upload&&upload.filename)||"";
    $("imp-name").value=fn.replace(/\.[^.]+$/,"")||"Imported dataset";
    setStep(3);await loadKpiPicker();
  }catch(e){$("imp-result").innerHTML=`<div class="banner err">${warnIcon()}${esc(e.message)}</div>`;}
}
async function confirmJoinBudget(){
  const targetId=$("imp-target").value;
  if(!targetId){$("imp-result").innerHTML=`<div class="banner err">${warnIcon()}Pick a target dataset first.</div>`;return;}
  const mapping=buildMapping();
  $("imp-result").innerHTML='<div class="loading"><span class="spin"></span> Matching &amp; attaching budget…</div>';
  try{
    const res=await api(`/ingest/${upload.upload_id}/join-budget`,{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({target_dataset_id:Number(targetId),mapping,metric_suffix:$("imp-suffix").value.trim()})});
    $("imp-result").innerHTML="";
    const nm=res.near_misses||[],um=res.unmatched_metrics||[],up_=res.unmatched_periods||[];
    let html=`<div class="banner ${nm.length||um.length||up_.length?"warn":"ok"}">
      <b>${plural(res.matched,"budget value")} attached.</b></div>`;
    if(nm.length)html+=`<div class="banner info" style="margin-top:10px"><b>Matched by spelling, not an exact name</b> — confirm these are right:
      <ul style="margin:6px 0 0 18px">${nm.map(x=>`<li>"${esc(x.upload_label)}" → <b>${esc(x.matched_metric)}</b></li>`).join("")}</ul></div>`;
    if(um.length)html+=`<div class="banner warn" style="margin-top:10px"><b>No matching metric found</b> for: ${um.map(esc).join(", ")}</div>`;
    if(up_.length)html+=`<div class="banner warn" style="margin-top:10px"><b>${plural(up_.length,"period")} had no actuals to attach a budget to</b> — the metric exists, but not for that month yet.</div>`;
    $("imp-join-result").style.display="";$("imp-join-result").innerHTML=html
      +`<div style="margin-top:16px"><button class="btn primary" onclick="finishBudgetJoin(${Number(targetId)})">Done — view dashboard</button>
         <button class="btn" style="margin-left:8px" onclick="resetImport()">Import another file</button></div>`;
  }catch(e){$("imp-result").innerHTML=`<div class="banner err">${warnIcon()}${esc(e.message)}</div>`;}
}
// QA #9: the dashboard showed pre-join facts ("vs plan –") until a full reload.
async function finishBudgetJoin(targetId){
  resetImport();
  if(targetId&&targetId!==activeDatasetId)await activateDataset(targetId);
  else await loadPeriods();
  nav("dashboard");
}
async function applyTpl(id){const t=await api(`/templates/${id}`);await confirmMapping(t.mapping);toast("Saved mapping applied");}
function humanizeKpiName(source){
  const raw=String(source||"").trim(),acronyms={arr:"ARR",mrr:"MRR",mql:"MQL",mqls:"MQLs",sql:"SQL",sqls:"SQLs",cogs:"COGS",cac:"CAC",ltv:"LTV",ebitda:"EBITDA",roi:"ROI",roas:"ROAS",nps:"NPS",usd:"USD",eur:"EUR",gbp:"GBP"};
  if(!raw.includes("_")&&raw!==raw.toLowerCase())return raw;
  const words=raw.toLowerCase().replace(/_(usd|eur|gbp)$/," ").trim().replace(/closed_won/g,"closed-won").split(/[_\s]+/);
  return words.map((w,i)=>acronyms[w]||(i===0?w.charAt(0).toUpperCase()+w.slice(1):w)).join(" ");
}
function kpiRows(){return [...document.querySelectorAll(".kpi-grid[data-src]")];}
function selectKpis(include){kpiRows().forEach(r=>r.querySelector(".k-include").checked=include);updateKpiSelection();}
function selectKpiEdits(select){kpiRows().forEach(r=>r.querySelector(".k-select").checked=select);updateKpiSelection();}
function updateKpiSelection(){
  const rows=kpiRows(),included=rows.filter(r=>r.querySelector(".k-include").checked).length,selected=rows.filter(r=>r.querySelector(".k-select").checked).length;
  // A tracked KPI with no direction yet: its variances can't be coloured good or bad.
  const undecided=rows.filter(r=>{const d=r.querySelector(".k-dir");return r.querySelector(".k-include").checked&&d&&!d.value;}).length;
  $("imp-finish").disabled=kpiLoading||kpiSaving||importLeaving||!included||!!undecided;
  $("kpi-selection").textContent=kpiSaving?"Finishing import…":kpiLoading?"Loading KPI choices…":!included?"Pick at least one KPI to finish."
    :undecided?`Choose which way is good for ${plural(undecided,"KPI")} (a rise in a cost is bad news).`:plural(included,"KPI")+" to track";
  $("kpi-bulk-apply").disabled=kpiLoading||kpiSaving||importLeaving||!selected;
  $("kpi-bulk-selection").textContent=selected?`Set category, unit or direction for ${plural(selected,"KPI")} at once`:"";
  // The shared editor only shows once rows are picked to edit together.
  const bulk=$("kpi-bulk");if(bulk)bulk.hidden=!selected;
}
function applyKpiBulk(){
  const selected=kpiRows().filter(r=>r.querySelector(".k-select").checked);
  if(!selected.length)return;
  const fields=[["kpi-bulk-category",".k-cat"],["kpi-bulk-unit",".k-unit"],["kpi-bulk-direction",".k-dir"]],changes=fields.filter(([id])=>$(id).value.trim());
  if(!changes.length){toast("Choose a category, unit or direction to apply");return;}
  selected.forEach(r=>changes.forEach(([id,field])=>{const el=r.querySelector(field);el.value=$(id).value.trim();el.classList?.remove("ask");}));
  updateKpiSelection();
  toast("Updated "+plural(selected.length,"KPI"));
}
async function loadKpiPicker(){
  const current=lastImport;kpiLoading=true;updateKpiSelection();
  $("kpi-rows").innerHTML='<div class="loading"><span class="spin"></span> Loading KPI choices…</div><div class="kpi-skeleton" aria-hidden="true"></div>';
  const slow=setTimeout(()=>{if(kpiLoading&&lastImport===current)$("kpi-selection").textContent="Still loading KPI choices…";},4000);
  try{
    const metrics=await api(`/ingest/${upload.upload_id}/metrics`);if(lastImport!==current)return;
    $("kpi-rows").innerHTML=metrics.length?`<div class="kpi-grid hdr"><span>Track · edit</span><span>Name</span><span>Category</span><span>Unit</span><span>Good direction</span></div>`+
      metrics.map(m=>{const s=m.suggestion||{},row=esc(m.source_metric);return`<div class="kpi-grid" data-src="${row}">
        <div class="kpi-checks"><label><input type="checkbox" class="k-include" checked aria-label="Include ${row}" onchange="updateKpiSelection()"> Track</label><label><input type="checkbox" class="k-select" aria-label="Select ${row} for bulk edit" onchange="updateKpiSelection()"> Edit together</label></div>
        <label class="kpi-field"><span class="kpi-field-label">Name</span><input class="k-name" aria-label="Display name for ${row}" value="${esc(humanizeKpiName(m.source_metric))}"><small class="kpi-source">Source: ${row}</small></label>
        <label class="kpi-field"><span class="kpi-field-label">Category</span><input class="k-cat" aria-label="Category for ${row}" value="${esc(s.category||"Uncategorized")}"></label>
        <label class="kpi-field"><span class="kpi-field-label">Unit</span><select class="k-unit" aria-label="Unit for ${row}">${[["USD","Amount"],["%","Percent"],["count","Count"]].map(([u,l])=>`<option value="${u}" ${u===(s.unit||"USD")?"selected":""}>${l}</option>`).join("")}</select></label>
        <label class="kpi-field"><span class="kpi-field-label">Good direction</span><select class="k-dir${s.direction_known===false?" ask":""}" aria-label="Direction for ${row}" onchange="this.classList.remove('ask');updateKpiSelection()">${s.direction_known===false?'<option value="" selected disabled>Which way is good?</option>':""}<option value="up" ${s.direction_known!==false&&(s.direction_good||"up")==="up"?"selected":""}>Up is good</option><option value="down" ${s.direction_known!==false&&s.direction_good==="down"?"selected":""}>Down is good</option></select></label></div>`;}).join(""):
      '<div class="banner info">No KPIs were found. Leave this import to delete the unfinished dataset, then upload a file with metric values.</div>';
  }catch(e){if(lastImport===current)$("kpi-rows").innerHTML=`<div class="banner err">${esc(e.message)} <button class="btn sm" onclick="loadKpiPicker()">Retry</button></div>`;}
  finally{clearTimeout(slow);if(lastImport===current){kpiLoading=false;updateKpiSelection();}}
}
async function confirmKpis(){
  if(kpiSaving||importLeaving)return;
  const kpis=[...document.querySelectorAll(".kpi-grid[data-src]")].filter(r=>r.querySelector(".k-include").checked).map(r=>({source_metric:r.dataset.src,display_name:r.querySelector(".k-name").value,category:r.querySelector(".k-cat").value,unit:r.querySelector(".k-unit").value,direction_good:r.querySelector(".k-dir").value}));
  if(!kpis.length){updateKpiSelection();return;}
  kpiSaving=true;updateKpiSelection();
  try{
  await api("/kpis",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kpis})});
  // Apply the chosen display name + domain to the freshly-created dataset —
  // by the id the import returned, never activeDatasetId (that would rename
  // the previously selected dataset).
  const name=($("imp-name").value||"").trim(),domain=$("imp-domain").value;
  const newId=lastImport&&lastImport.datasetId;
  if(newId!=null&&(name||domain)){
    try{await api(`/datasets/${newId}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:name||undefined,domain,currency:$("imp-currency")?.value||undefined})});}
    catch(e){toast("Imported, but couldn't save the dataset name: "+e.message);}
  }
  const li=lastImport||{};resetImport();await loadPeriods();nav("dashboard");
  // Show a success banner on the dashboard with a one-click Generate action.
  const el=$("import-banner");
  if(el){el.style.display="";el.className="banner ok";
    el.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
      <span>Imported ${plural(li.rows||0,"row")}: ${plural(kpis.length,"KPI","KPIs")} across ${plural(li.periods||0,"period")}. Next, draft the commentary.</span>
      <button class="write-only btn sm primary" style="margin-left:auto" onclick="$('import-banner').style.display='none';generateAll()">Draft commentary</button>
      <button class="ico" style="border:none" onclick="$('import-banner').style.display='none'" aria-label="Dismiss">${XICON}</button>`;
  }
  }catch(e){$("imp-result").innerHTML=`<div class="banner err">Import failed to finalize: ${esc(e.message)} <button class="write-only btn sm" onclick="confirmKpis()">Retry</button></div>`;}
  finally{kpiSaving=false;updateKpiSelection();}
}

// Entry point: boot() handles auth (creates the Supabase client, checks the
// session) and then calls startApp(), which loads periods and applies the hash.
console.log("Closebrief UI build: v5.0.0 (ledger identity: green ink, serif brief, mono figures, verified seal)");
boot();

/* ---------- dialog focus ----------
   While an overlay (.cmdk-back.open) is showing, Tab and Shift+Tab cycle inside
   its dialog, focus starts inside it, and closing it returns focus to whatever
   opened it. One observer covers every overlay, however it is opened or closed. */
const FOCUSABLE='a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
function openOverlay(){return [...document.querySelectorAll(".cmdk-back.open")].pop()||null;}
function dialogFocusables(back){
  return [...back.querySelectorAll(FOCUSABLE)].filter(el=>el.offsetParent!==null||el===document.activeElement);
}
function trapDialogTab(e){
  if(e.key!=="Tab")return false;
  const back=openOverlay();if(!back)return false;
  const items=dialogFocusables(back);
  if(!items.length){e.preventDefault();return true;}
  const first=items[0],last=items[items.length-1],inside=back.contains(document.activeElement);
  if(e.shiftKey&&(document.activeElement===first||!inside)){e.preventDefault();last.focus();return true;}
  if(!e.shiftKey&&(document.activeElement===last||!inside)){e.preventDefault();first.focus();return true;}
  return false;
}
const _dialogReturn=new Map();
function watchDialogs(){
  if(typeof MutationObserver==="undefined")return;
  document.querySelectorAll(".cmdk-back").forEach(back=>{
    new MutationObserver(()=>{
      const open=back.classList.contains("open");
      if(open&&!_dialogReturn.has(back)){
        _dialogReturn.set(back,document.activeElement);
        setTimeout(()=>{if(!back.contains(document.activeElement)){const f=dialogFocusables(back)[0];if(f)f.focus();}},60);
      }else if(!open&&_dialogReturn.has(back)){
        const el=_dialogReturn.get(back);_dialogReturn.delete(back);
        if(el&&el.isConnected&&typeof el.focus==="function")el.focus();
      }
    }).observe(back,{attributes:true,attributeFilter:["class"]});
  });
}
document.addEventListener("keydown",e=>{trapDialogTab(e);},true);
watchDialogs();

/* ---------- delegated actions ----------
   Buttons built from user text (KPI names, note titles, periods) carry it in
   data-arg instead of an inline onclick string, so a quote or tag in a name can
   never break out into code. */
const DATA_ACTIONS={
  close:a=>selectClose(a),
  metric:a=>openMetricByName(a),
  reason:a=>addReasonFor(a),
  source:a=>showSourceDoc(a),
  "sync-now":a=>syncSourceNow(Number(a)),
  "sync-remove":a=>removeSyncSource(Number(a)),
  dismiss:(a,el)=>{try{localStorage.setItem(a,"1");}catch{} const t=$(el.dataset.target);if(t)t.style.display="none";},
};
document.addEventListener("click",e=>{
  const el=e.target.closest&&e.target.closest("[data-act]");if(!el)return;
  const fn=DATA_ACTIONS[el.dataset.act];if(fn){e.preventDefault();fn(el.dataset.arg||"",el,e);}
});
/* ---------- end delegated actions ---------- */
