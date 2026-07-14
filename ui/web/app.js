const $=id=>document.getElementById(id);
let currentPeriod=null, currentFacts=[], detailFact=null, activeDatasetId=null, searchQuery="", datasetsCache=[];
/* auth state */
let sb=null, authToken=null, authMode="signin", currentUser={email:"local@dev",role:"admin"};

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
const BRAND={green:"#1e6e50",greenDark:"#4fae83",red:"#b42e2c"};
function toggleExport(e){e.stopPropagation();$("export-menu").classList.toggle("open");}
function reportDelta(label,v,downGood){
  const p=pct(v);if(p===null)return`<span class="rd flat">${label} –</span>`;
  const good=downGood?v<0:v>0,col=v===0?"#5c6a62":good?BRAND.green:BRAND.red,bg=v===0?"#f1f3f0":good?"#e9f3ee":"#faeeed";
  return`<span class="rd" style="color:${col};background:${bg}">${v>0?"▲":v<0?"▼":"■"} ${label} ${p}</span>`;
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
      <div class="rc-deltas">${reportDelta("MoM",f.deltas.mom_pct,dg)}${reportDelta("vs plan",f.deltas.budget_var_pct,dg)}${f.is_anomaly?'<span class="rd" style="color:#b42e2c;background:#faeeed">ANOMALY</span>':""}</div>
      <div class="rc-narr">${f.narrative?esc(f.narrative):"<i>No narrative generated for this metric.</i>"}</div>
      ${src?`<div class="rc-src">${src}</div>`:""}
    </div>`;
  }).join("");
  const today=new Date().toLocaleDateString(undefined,{year:"numeric",month:"long",day:"numeric"});
  return`<!doctype html><html><head><meta charset="utf-8"><title>Closebrief Report ${esc(currentPeriod||"")}</title>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    :root{--green2:${BRAND.green};--red:${BRAND.red};--mut2:#8a948c}
    body{font-family:'Plus Jakarta Sans',system-ui,-apple-system,Segoe UI,sans-serif;color:#16211b;padding:28px 32px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
    @font-face{font-family:'Plus Jakarta Sans';font-weight:400;src:url(/vendor/fonts/pjs-400.woff2) format('woff2')}
    @font-face{font-family:'Plus Jakarta Sans';font-weight:600;src:url(/vendor/fonts/pjs-600.woff2) format('woff2')}
    @font-face{font-family:'Plus Jakarta Sans';font-weight:700;src:url(/vendor/fonts/pjs-700.woff2) format('woff2')}
    @font-face{font-family:'Plus Jakarta Sans';font-weight:800;src:url(/vendor/fonts/pjs-800.woff2) format('woff2')}
    @font-face{font-family:'Newsreader';font-weight:400 700;src:url(/vendor/fonts/newsreader.woff2) format('woff2')}
    @font-face{font-family:'IBM Plex Mono';font-weight:500;src:url(/vendor/fonts/plexmono-500.woff2) format('woff2')}
    .rhead{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2.5px solid ${BRAND.green};padding-bottom:14px;margin-bottom:18px}
    .rhead .mark{display:inline-flex;align-items:center;gap:9px}
    .rhead .mk{width:26px;height:26px;border-radius:7px;background:${BRAND.green};display:inline-flex;align-items:center;justify-content:center}
    .rhead h1{font-family:'Newsreader',Georgia,serif;font-size:20px;font-weight:600;letter-spacing:-.005em}
    .rhead .sub{font-size:12px;color:#5c6a62;margin-top:2px}
    .rhead .r{text-align:right;font-size:11.5px;color:#5c6a62;line-height:1.6}
    .rsummary{display:flex;gap:26px;background:#f4f7f4;border:1px solid #e2e8e2;border-radius:10px;padding:13px 18px;margin-bottom:20px}
    .rsummary div{font-size:11.5px;color:#5c6a62;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
    .rsummary b{display:block;font-family:'IBM Plex Mono',Menlo,monospace;font-size:19px;color:#16211b;font-weight:500;margin-top:3px;text-transform:none;letter-spacing:0}
    .rgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
    .rcard{border:1px solid #e2e8e2;border-radius:11px;padding:15px 16px;break-inside:avoid}
    .rc-top{display:flex;align-items:center;gap:7px;margin-bottom:8px}
    .rc-dot{width:8px;height:8px;border-radius:50%;flex:none}
    .rc-name{font-weight:700;font-size:14px}
    .rc-cat{margin-left:auto;font-size:10px;color:#8a948c;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
    .rc-vrow{display:flex;align-items:flex-end;justify-content:space-between;gap:10px;margin-bottom:9px}
    .rc-val{font-family:'IBM Plex Mono',Menlo,monospace;font-size:23px;font-weight:500;letter-spacing:-.02em}
    .rc-deltas{display:flex;gap:7px;margin-bottom:9px}
    .rd{font-size:11px;font-weight:700;padding:3px 8px;border-radius:6px}
    .rd.flat{color:#5c6a62;background:#f1f3f0}
    .rc-narr{font-family:'Newsreader',Georgia,serif;font-size:13.5px;line-height:1.6;color:#33413a}
    .rc-src{margin-top:9px;display:flex;flex-wrap:wrap;gap:5px}
    .src{font-size:9.5px;font-weight:700;color:${BRAND.green};background:#e9f3ee;border:1px solid #c6ddd1;padding:2px 7px;border-radius:5px}
    .rfoot{margin-top:22px;padding-top:12px;border-top:1px solid #e2e8e2;font-size:10.5px;color:#8a948c;text-align:center}
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
      <div>Top mover vs plan<b style="font-size:14px">${mover?esc(mover.metric)+" ("+(pct(mover.deltas.budget_var_pct)||"–")+")":"—"}</b></div>
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
    const dark=document.documentElement.getAttribute("data-theme")==="dark";
    const BG=dark?"171F1A":"FFFFFF",INK=dark?"E9EEE9":"16211B",MUT=dark?"93A096":"5C6A62",ACC=(dark?BRAND.greenDark:BRAND.green).slice(1).toUpperCase();
    const p=new PptxGenJS();p.layout="LAYOUT_16x9";   // 10 x 5.63in default
    const bg={color:BG};
    // Title slide
    let s=p.addSlide();s.background=bg;
    s.addText("FP&A Executive Report",{x:0.6,y:1.8,w:8.8,h:0.8,fontSize:34,bold:true,color:INK,fontFace:"Segoe UI"});
    s.addText(`${currentPeriod||""}`,{x:0.6,y:2.7,w:8.8,h:0.5,fontSize:20,color:ACC,fontFace:"Segoe UI"});
    s.addText(`Generated ${new Date().toLocaleDateString()} · Powered by Closebrief`,{x:0.6,y:4.7,w:8.8,h:0.4,fontSize:12,color:MUT,fontFace:"Segoe UI"});
    // Summary slide
    const anom=facts.filter(f=>f.is_anomaly).length;
    const mover=facts.filter(f=>f.deltas&&f.deltas.budget_var_pct!=null).sort((a,b)=>Math.abs(b.deltas.budget_var_pct)-Math.abs(a.deltas.budget_var_pct))[0];
    s=p.addSlide();s.background=bg;
    s.addText("Summary",{x:0.6,y:0.4,w:8.8,h:0.6,fontSize:24,bold:true,color:INK,fontFace:"Segoe UI"});
    const rows=[["KPIs tracked",String(facts.length)],["Anomalies flagged",String(anom)],["Top mover vs plan",mover?`${mover.metric} (${pct(mover.deltas.budget_var_pct)||"–"})`:"—"]];
    rows.forEach((r,i)=>{s.addText(r[0],{x:0.8,y:1.4+i*0.9,w:4,h:0.6,fontSize:16,color:MUT,fontFace:"Segoe UI"});
      s.addText(r[1],{x:4.8,y:1.4+i*0.9,w:4.6,h:0.6,fontSize:16,bold:true,color:INK,fontFace:"Segoe UI"});});
    // One slide per metric
    facts.forEach(f=>{
      const s=p.addSlide();s.background=bg;const dg=f.direction_good==="down";
      s.addText(f.metric,{x:0.6,y:0.4,w:8.8,h:0.6,fontSize:22,bold:true,color:INK,fontFace:"Segoe UI"});
      s.addText(f.category||"",{x:0.6,y:0.95,w:8.8,h:0.35,fontSize:12,color:MUT,fontFace:"Segoe UI"});   // PPTX text is not HTML — no esc()
      s.addText(fmt(f.value,f.unit),{x:0.6,y:1.4,w:5,h:0.9,fontSize:40,bold:true,color:INK,fontFace:"Segoe UI"});
      const mom=pct(f.deltas.mom_pct),bud=pct(f.deltas.budget_var_pct);
      s.addText(`MoM ${mom||"–"}    vs plan ${bud||"–"}`,{x:0.6,y:2.4,w:8.8,h:0.4,fontSize:13,bold:true,color:ACC,fontFace:"Segoe UI"});
      s.addText(f.narrative||"No narrative generated.",{x:0.6,y:2.95,w:8.8,h:2.2,fontSize:13,color:INK,fontFace:"Segoe UI",valign:"top"});
      if(f.sources&&f.sources.length)s.addText("Sources: "+f.sources.map(x=>x.title||x.id).join(", "),{x:0.6,y:5.0,w:8.8,h:0.4,fontSize:10,italic:true,color:MUT,fontFace:"Segoe UI"});
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
  const pages=[["dashboard","Insights Dashboard"],["digest","Executive Digest"],["context","Context Library"],["import","Import & Mapping"],["costs","Cost & Usage"],["notifications","Notifications"]];
  if(currentUser.role==="admin")pages.push(["admin","User Management"]);
  const acts=[
    {label:"Generate all narratives",icon:CI.bolt,run:()=>generateAll(),group:"Actions"},
    {label:"Export report (PDF)",icon:CI.down,run:()=>exportPDF(),group:"Actions"},
    {label:"Export report (PowerPoint)",icon:CI.down,run:()=>exportPPTX(),group:"Actions"},
    {label:"Toggle dark mode",icon:CI.sun,run:()=>toggleTheme(),group:"Actions"},
  ];
  const pageItems=pages.map(([v,l])=>({label:l,icon:CI.page,run:()=>nav(v),group:"Pages"}));
  const metricItems=(currentFacts||[]).filter(f=>f.has_data).map(f=>({label:f.metric,sub:f.category||"",icon:CI.metric,
    run:()=>{nav("dashboard");const i=shownFacts.indexOf(f);(i>=0)?openDetail(i):(currentFacts.includes(f)&&(searchQuery="",renderCards(),openDetail(shownFacts.indexOf(f))));},group:"Metrics"}));
  return[...acts,...pageItems,...metricItems];
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
      <button class="row-del" title="Remove from board" onclick="kpimRemove(${i})">✕</button>
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

function toast(m){const t=$("toast");t.textContent=m;t.classList.add("show");setTimeout(()=>t.classList.remove("show"),2600);}
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
  if(authToken){opts.headers={...(opts.headers||{}),Authorization:"Bearer "+authToken};}
  else if(demoSession){opts.headers={...(opts.headers||{}),"X-Closebrief-Demo":"1"};}
  // Tenant scope: the server validates membership, so a spoofed id can't leak data.
  if(activeWorkspaceId!=null)opts.headers={...(opts.headers||{}),"X-Workspace-Id":String(activeWorkspaceId)};
  const r=await fetch(path,opts);
  if(r.status===401){
    // A stored session's access-token expires after ~1h. Before bouncing to
    // login, silently refresh it once and retry — avoids a login-page flash.
    if(sb&&!_retried){
      try{
        const {data}=await sb.auth.refreshSession();
        if(data&&data.session&&data.session.access_token){
          authToken=data.session.access_token;
          return api(path,opts,true);
        }
      }catch{}
    }
    showLogin();throw new Error("Session expired — please sign in");
  }
  if(!r.ok&&r.status!==503){let d=r.statusText;try{d=(await r.json()).detail||d;}catch{}throw new Error(d);}
  if(r.status===204)return null; return r.json();
}
const fmt=(v,unit)=>{
  if(v===null||v===undefined)return"–";
  if(unit==="%")return(+v.toFixed(1))+"%";
  if(unit==="count")return Math.round(v).toLocaleString();
  // Finance-style: sign BEFORE the currency symbol (-$1.20M / -$370K / -$12.50).
  const a=Math.abs(v),sign=v<0?"-":"";let mag;
  if(a>=1e6)mag="$"+(a/1e6).toFixed(2)+"M"; else if(a>=1e3)mag="$"+(a/1e3).toFixed(0)+"K"; else mag="$"+(+a.toFixed(2)).toLocaleString();
  return sign+mag;
};
const pct=v=>v===null||v===undefined?null:(v>0?"+":"")+v.toFixed(1)+"%";
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const plural=(n,one,many)=>`${n} ${n===1?one:(many||one+"s")}`;
const MONTHS=["January","February","March","April","May","June","July","August","September","October","November","December"];
function fmtPeriodTitle(p){
  if(!p)return"Dashboard";
  if(/^\d{4}-\d{2}$/.test(p))return `${MONTHS[+p.slice(5,7)-1]} ${p.slice(0,4)} close`;
  if(/^\d{4}-Q\d$/.test(p))return `Q${p.slice(6)} ${p.slice(0,4)} overview`;
  return `${p} overview`;
}

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
    body:`<div style="font-size:13.5px;line-height:1.55;color:var(--ink2)">${esc(message)}</div>`,getValue:()=>true});
}

/* tooltip */
const tip=$("tip");
function showTip(h,x,y){tip.innerHTML=h;tip.style.opacity=1;tip.style.left=Math.min(x+14,innerWidth-190)+"px";tip.style.top=(y-10)+"px";}
function hideTip(){tip.style.opacity=0;}

/* ---------- nav ---------- */
function toggleSidebar(){document.body.classList.toggle("sidebar-open");}
function closeSidebar(){document.body.classList.remove("sidebar-open");}
function toggleCollapse(){
  const c=document.body.classList.toggle("side-collapsed");
  try{localStorage.setItem("cb-side-collapsed",c?"1":"0");}catch{}
}
// Restore collapsed state on load.
try{if(localStorage.getItem("cb-side-collapsed")==="1")document.body.classList.add("side-collapsed");}catch{}
function nav(v){
  closeSidebar();   // dismiss the mobile drawer on navigation
  for(const s of["dashboard","detail","digest","context","analytics","review","import","costs","notifications","admin"])$("v-"+s).style.display=s===v?"":"none";
  for(const s of["dashboard","digest","context","analytics","review","import","costs","notifications","admin"])
    document.querySelector(`[data-view=${s}]`)?.classList.toggle("active",s===v||(v==="detail"&&s==="dashboard"));
  if(v!=="detail"&&location.hash!=="#"+v)history.replaceState(null,"","#"+v);
  if(v==="context")loadContext();
  if(v==="analytics")loadAnalytics();
  if(v==="review")loadReview();
  if(v==="digest")loadDigest(false);
  if(v==="costs")loadCosts();
  if(v==="notifications")loadNotifications();
  if(v==="admin")loadAdmin();
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
let demoSession=false, appCfg={};
function enterDemo(){
  demoSession=true;authToken=null;appStarted=false;
  document.body.classList.add("demo");
  const b=$("demo-bar");if(b)b.style.display="flex";
  hideLogin();startApp();
}
function exitDemo(signup){
  demoSession=false;document.body.classList.remove("demo");
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
  if(cfg.demo_enabled){const b=$("lg-demo");if(b)b.style.display="";}
  if(!cfg.auth_enabled){startApp();return;}                    // local-dev bypass
  if(!window.supabase||!window.supabase.createClient){
    showLogin();
    const err=$("lg-err");
    if(err){err.textContent="Auth library failed to load (/vendor/supabase.js). Refresh the page.";err.style.display="block";}
    return;
  }
  sb=window.supabase.createClient(cfg.supabase_url,cfg.supabase_anon_key);
  // React to sign-in / token-refresh events (the source of truth for the token).
  sb.auth.onAuthStateChange((event,s)=>{
    if(s&&s.access_token){authToken=s.access_token; if(event==="SIGNED_IN"||event==="INITIAL_SESSION")onSession(s);}
    else if(event==="SIGNED_OUT"){authToken=null;appStarted=false;showLogin();}
  });
  let {data:{session}}=await sb.auth.getSession();
  // If the stored token is expiring soon, refresh it up front so the first API
  // call already carries a valid token (prevents the login-page flash).
  if(session&&session.expires_at&&session.expires_at*1000-Date.now()<60000){
    try{const {data}=await sb.auth.refreshSession(); if(data&&data.session)session=data.session;}catch{}
  }
  if(session&&session.access_token)onSession(session); else showLogin();
}
function showLogin(){$("login-gate").classList.add("show");$("app-root").style.display="none";}
function hideLogin(){$("login-gate").classList.remove("show");$("app-root").style.display="flex";}
function toggleAuthMode(){
  authMode=authMode==="signin"?"signup":"signin";
  $("lg-title").textContent=authMode==="signin"?"Sign in":"Create your account";
  $("lg-psub").textContent=authMode==="signin"?"Welcome back. Sign in to your workspace.":"The first account becomes the workspace admin.";
  $("lg-btn").textContent=authMode==="signin"?"Sign in":"Sign up";
  $("lg-toggle").innerHTML=authMode==="signin"?'Need an account? <a onclick="toggleAuthMode()">Sign up</a>':'Have an account? <a onclick="toggleAuthMode()">Sign in</a>';
}
async function submitAuth(){
  const email=$("lg-email").value.trim(), pass=$("lg-pass").value, err=$("lg-err");
  err.style.display="none";
  if(!sb){err.textContent="Auth client not initialized — the auth library failed to load. Hard-refresh (Ctrl+Shift+R) and check the browser console.";err.style.display="block";return;}
  if(!email||!pass){err.textContent="Enter email and password.";err.style.display="block";return;}
  $("lg-btn").disabled=true;$("lg-btn").textContent="…";
  try{
    const fn=authMode==="signin"?sb.auth.signInWithPassword({email,password:pass}):sb.auth.signUp({email,password:pass});
    const {data,error}=await fn;
    if(error)throw error;
    if(data.session)onSession(data.session);
    else{err.textContent="Check your email to confirm your account, then sign in.";err.style.display="block";toggleAuthMode();}
  }catch(e){err.textContent=e.message||"Authentication failed.";err.style.display="block";}
  finally{$("lg-btn").disabled=false;$("lg-btn").textContent=authMode==="signin"?"Sign in":"Sign up";}
}
async function onSession(session){
  authToken=session.access_token;
  hideLogin();
  if(appStarted)return;            // don't re-run the full load on token refresh
  appStarted=true;
  await startApp();
}
async function signOut(){ appStarted=false; if(sb)await sb.auth.signOut(); authToken=null; location.reload(); }
async function startApp(){
  if(authToken){
    try{currentUser=await api("/me");}catch{currentUser={email:"user",role:"analyst"};}
    applyRole(currentUser);
    try{activeWorkspaceId=+localStorage.getItem("cb-ws")||null;}catch{}
    await loadWorkspaces();   // resolve tenant scope before loading any data
  }else if(demoSession){currentUser={email:"demo@closebrief.app",role:"viewer"};applyRole(currentUser);}
  else{applyRole({email:"local@dev",role:"admin"});}
  loadPeriods().then(()=>{const h=location.hash.slice(1);
    if(["dashboard","digest","context","analytics","review","import","costs","notifications","admin"].includes(h))nav(h);
    else if(h.startsWith("metric"))openMetricFromHash(h);
  });
}
function applyRole(u){
  document.documentElement.setAttribute("data-role",u.role);
  $("user-email").textContent=u.email;
  $("user-avatar").textContent=(u.email||"?").slice(0,2).toUpperCase();
  $("user-role").innerHTML=`<span class="role-badge ${u.role}">${u.role}</span>`;
  $("logout-btn").style.display=authToken?"":"none";
  $("nav-admin").style.display=u.role==="admin"?"":"none";
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
    if(!f)return;
    nav("dashboard");const i=shownFacts.indexOf(f);
    if(i>=0)openDetail(i);else{searchQuery="";renderCards();openDetail(shownFacts.indexOf(f));}
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
  box.innerHTML=`<div class="rc-panel">
    <div class="rc-head"><span class="rc-title">Root cause — ${esc(rc.primary_factor||"")}</span>${z}</div>
    ${bars}${trend}${drivers}
    <div style="margin-top:12px"><button class="btn sm write-hide" id="d-rc-narr-btn" onclick="rootCauseNarrative()">Explain (AI)</button></div>
    <div id="d-rc-narr" style="margin-top:10px"></div></div>`;
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
  if(!currentPeriod){toast("Import data first");return;}
  const btn=$("export-pack-btn");if(btn)btn.disabled=true;
  try{
    // Fetch with auth headers (a plain new-tab URL wouldn't carry the bearer),
    // then open the returned HTML from a blob so it prints/saves cleanly.
    const headers={};
    if(authToken)headers.Authorization="Bearer "+authToken;
    else if(demoSession)headers["X-Closebrief-Demo"]="1";
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

/* ================= ADVANCED ANALYTICS (v5.0) ================= */
let _anMetrics=[], _lastForecast=null, _scnT=null;
function _metricList(){return (currentFacts||[]).filter(f=>f.has_data).map(f=>({name:f.metric,unit:f.unit}));}
function _fillMetricSelect(id){
  const sel=$(id);if(!sel)return;const prev=sel.value;
  sel.innerHTML=_anMetrics.length?_anMetrics.map(m=>`<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("")
    :'<option value="">No metrics — import data first</option>';
  if(prev&&_anMetrics.some(m=>m.name===prev))sel.value=prev;
}
async function loadAnalytics(){
  // currentFacts is normally populated by the dashboard; on a deep-link, fetch it.
  if(!(currentFacts&&currentFacts.length)&&currentPeriod){
    try{currentFacts=await api(`/facts?period=${encodeURIComponent(currentPeriod)}&granularity=${granularity}`);}catch{}
  }
  _anMetrics=_metricList();
  _fillMetricSelect("fc-metric");_fillMetricSelect("scn-metric");
  const chips=$("kb-chips");
  if(chips)chips.innerHTML=_anMetrics.length
    ?_anMetrics.map((m,i)=>`<span class="fchip" onclick="insertMetricToken(${i})">${esc(m.name)}</span>`).join("")
    :'<span class="an-empty">No metrics yet — import data first.</span>';
  onLever();
  $("scn-out").innerHTML='<div class="an-empty">Pick a metric and move a lever to project.</div>';
  loadCrossDomain();
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
      `<div class="stat" style="--sc:var(--mut)"><div class="stat-lbl">Backtest MAPE</div><div class="card-value">${mape}</div>`+
      `${thin?'<div class="psub" style="margin-top:2px">Only '+(fc.n_history||0)+' periods — treat as indicative</div>':""}</div>`;
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
    const chips=$("kb-chips");if(chips)chips.innerHTML=_anMetrics.map((m,i)=>`<span class="fchip" onclick="insertMetricToken(${i})">${esc(m.name)}</span>`).join("");
  }catch(e){toast("Failed: "+errMsg(e));}
  finally{btn.disabled=false;}
}
/* ---- Cross-dataset signals ---- */
async function loadCrossDomain(){
  const box=$("xd-list");if(!box)return;box.innerHTML='<div class="an-empty">Loading…</div>';
  try{
    const rows=await api("/insights/cross-domain");
    if(!rows.length){box.innerHTML='<div class="an-empty">Need at least two datasets with overlapping periods to surface cross-dataset signals.</div>';return;}
    box.innerHTML=rows.slice(0,12).map(p=>{
      const cls=p.direction==="positive"?"pos":"neg";
      const lag=p.lag?`${Math.abs(p.lag)}-period ${p.lag>0?"lead":"lag"}`:"same period";
      return `<div class="xd-item"><div class="xd-r ${cls}">${p.r>0?"+":""}${p.r}</div>
        <div class="xd-body"><div class="xd-pair">${esc(p.metric_a)} <span class="xd-arrow">↔</span> ${esc(p.metric_b)}</div>
        <div class="xd-meta">${esc(p.dataset_a)} → ${esc(p.dataset_b)} · ${lag} · ${p.months} mo overlap</div></div></div>`;
    }).join("");
  }catch(e){box.innerHTML=`<div class="an-empty">${esc(errMsg(e))}</div>`;}
}
/* ---- Collaborative review (detail view) ---- */
function renderReviewStrip(f){
  const strip=$("d-rev-strip");if(!strip)return;
  if(!f||!f.report_id){strip.style.display="none";$("d-versions").style.display="none";$("d-assign-row").style.display="none";return;}
  strip.style.display="";
  const st=f.review_status||"none",badge=$("d-rev-badge");
  badge.className="rev-badge "+st;
  badge.textContent=({pending:"Pending review",approved:"Approved",changes_requested:"Changes requested"})[st]||"Not reviewed";
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
    toast(status==="approved"?"Narrative approved":"Changes requested");
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
  const box=$("rq-list");if(box)box.innerHTML='<div class="an-empty">Loading…</div>';
  let items=[];
  try{items=await api("/reports/review-queue?all_datasets=true");}
  catch(e){if(box)box.innerHTML=`<div class="an-empty">${esc(errMsg(e))}</div>`;return;}
  _reviewItems=items;updateReviewBadge(items.length);
  if(!box)return;
  if(!items.length){box.innerHTML='<div class="an-empty">Nothing awaiting your review. Narratives assigned to you across your datasets show up here.</div>';return;}
  box.innerHTML=items.map((it,i)=>`<div class="rq-item" onclick="openReviewItem(${i})">
    <div class="rq-main"><div class="rq-metric">${esc(it.metric)}</div>
    <div class="rq-preview">${esc(it.preview||"No preview")}</div>
    <div class="rq-meta">${esc(it.dataset_name||"")}</div></div>
    <span class="rq-period">${esc(it.period)}</span>
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
  $("nt-loading").style.display="";
  try{
    const cfgs=await api("/notifications/configs");
    const icon={email:ICON_PATHS.mail,slack:ICON_PATHS.chat,webhook:ICON_PATHS.link};
    $("nt-list").innerHTML=cfgs.length?cfgs.map(c=>{
      const cf=c.config||{},target=cf.recipients?cf.recipients.join(", "):(cf.webhook_url||cf.url||"—");
      const events=(cf.events||["all"]).join(", ");
      return`<div class="card" style="margin-top:12px;flex-direction:row;align-items:center;gap:14px">
        <span class="nt-ic"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${icon[c.channel]||ICON_PATHS.bell}</svg></span>
        <div style="flex:1;min-width:0">
          <b style="text-transform:capitalize">${esc(c.channel)}</b>
          <div style="font-size:12px;color:var(--mut);word-break:break-all">${esc(target)}</div>
          <div style="font-size:11px;color:var(--mut2);margin-top:2px">Events: ${esc(events)}</div>
        </div>
        <label style="font-size:12px;color:var(--ink3);font-weight:600;display:flex;align-items:center;gap:6px">
          <input type="checkbox" ${c.enabled?"checked":""} onchange="ntToggle(${c.id},this.checked)"> Enabled</label>
        <button class="btn sm" onclick="ntTest(${c.id},this)">Send test</button>
        <button class="ico" title="Delete channel" aria-label="Delete channel" onclick="ntDelete(${c.id})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px">${ICON_PATHS.trash}</svg></button>
      </div>`;
    }).join(""):'<div class="empty-state">No channels configured yet. Add one above to receive alerts.</div>';
  }catch(e){toast(e.message);}
  finally{$("nt-loading").style.display="none";}
  loadSchedules();
}
/* ---------- schedules (Phase 2+4) ---------- */
function schKindFields(){const k=$("sch-kind").value;$("sch-topn-wrap").style.display=k==="digest"?"":"none";$("sch-recip-wrap").style.display=k==="board_pack"?"":"none";}
const CADENCE_LABEL={daily:"Daily",weekly:"Weekly",monthly:"Monthly"};
async function loadSchedules(){
  let jobs;try{jobs=await api("/schedules");}catch{return;}   // admin-only; ignore if forbidden
  const kindLabel={digest:"Scheduled digest",anomaly_scan:"Anomaly scan",board_pack:"Board pack email"};
  $("sch-list").innerHTML=jobs.length?jobs.map(j=>`
    <div class="card" style="margin-top:12px;flex-direction:row;align-items:center;gap:14px">
      <span class="nt-ic"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${j.kind==="digest"?'<path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h8M8 17h5"/>':'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>'}</svg></span>
      <div style="flex:1;min-width:0">
        <b>${esc(kindLabel[j.kind]||j.kind)}</b>
        <div style="font-size:12px;color:var(--mut)">${esc(CADENCE_LABEL[j.cadence]||j.cadence)}${j.kind==="digest"&&j.config&&j.config.top_n?` · top ${j.config.top_n}`:""}${j.next_run_at?` · next ${esc(String(j.next_run_at).slice(0,16).replace("T"," "))}`:""}</div>
        ${j.last_status?`<div style="font-size:11px;margin-top:2px;color:${j.last_status==="error"?"var(--red)":j.last_status==="ok"?"var(--green)":"var(--mut2)"}">last run: ${esc(j.last_status)}${j.fail_count?` · ${j.fail_count} consecutive failures`:""}${j.last_status==="error"&&j.last_error?` — ${esc(String(j.last_error).slice(0,80))}`:""}</div>`:""}
      </div>
      <label style="font-size:12px;color:var(--ink3);font-weight:600;display:flex;align-items:center;gap:6px">
        <input type="checkbox" ${j.enabled?"checked":""} onchange="schToggle(${j.id},this.checked)"> Enabled</label>
      <button class="ico" title="Delete schedule" aria-label="Delete schedule" onclick="schDelete(${j.id})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px">${ICON_PATHS.trash}</svg></button>
    </div>`).join(""):'<div class="empty-state" style="padding:26px">No schedules yet. Add one above to run digests or anomaly scans automatically.</div>';
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
      body:`<div style="font-size:13.5px;line-height:1.6;color:var(--ink2)">
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
  try{await api(`/notifications/test/${id}`,{method:"POST"});toast("Test sent ✓");}
  catch(e){toast(e.message||"Test failed");}
  finally{btn.disabled=false;btn.textContent="Send test";}
}
function inviteUser(){
  const url=location.origin;
  _openModalRaw({title:"Add a user",okLabel:"Copy invite link",
    body:`<div style="font-size:13.5px;line-height:1.6;color:var(--ink2)">
      <p style="margin-bottom:10px">Closebrief uses Supabase sign-in. To add a teammate:</p>
      <ol style="margin:0 0 12px 18px;display:flex;flex-direction:column;gap:6px">
        <li>Send them this link — they create their own account:</li>
      </ol>
      <div style="display:flex;gap:8px;margin-bottom:12px"><input class="modal-field" id="invite-url" value="${esc(url)}" readonly></div>
      <p style="color:var(--mut2);font-size:12.5px">New accounts join as <b>analyst</b> by default. Once they've signed in they appear in the table below, where you can change their role.</p>
    </div>`,
    getValue:()=>{const el=$("invite-url");el.select();try{navigator.clipboard.writeText(el.value);}catch{}toast("Invite link copied");return true;}});
}
async function loadAdmin(){
  try{
    const users=await api("/admin/users");
    $("admin-rows").innerHTML=users.map(u=>`<tr>
      <td><b>${esc(u.email)}</b></td>
      <td style="color:var(--mut2);font-family:ui-monospace,Menlo,monospace;font-size:11px">${esc(u.user_id).slice(0,18)}…</td>
      <td><select onchange="setUserRole('${esc(u.user_id)}',this.value)">
        ${["analyst","executive","admin"].map(r=>`<option ${u.role===r?"selected":""}>${r}</option>`).join("")}</select></td>
      <td style="color:var(--mut);white-space:nowrap">${esc((u.created_at||"").slice(0,10))}</td></tr>`).join("")
      ||'<tr><td colspan="4" class="loading">No users yet.</td></tr>';
  }catch(e){toast("Admin: "+e.message);}
}
async function setUserRole(uid,role){
  try{await api(`/admin/users/${uid}/role?role=${role}`,{method:"PUT"});toast("Role updated");}
  catch(e){toast(e.message);loadAdmin();}
}

/* ---------- datasets ---------- */
const DOMAIN_LABEL={fpa:"FP&A",marketing:"Marketing",ops:"Ops"};
let activeDatasetObj=null, activeDomain=null;
async function loadDatasets(){
  const d=await api("/datasets");
  activeDatasetId=d.active_id;
  datasetsCache=d.datasets;                       // for id -> name lookups (no strings in onclick)
  const active=d.datasets.find(x=>x.is_active);
  activeDatasetObj=active||null;
  // The active domain drives funnel rendering (marketing defines an ordered funnel).
  try{activeDomain=await api("/domain");}catch{activeDomain=null;}
  $("ws-name").textContent=active?active.name:"No dataset";
  const rows=d.datasets.map(ds=>`
    <div class="ws-item" onclick="activateDataset(${ds.id})">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="${ds.is_active?'var(--accent)':'var(--mut2)'}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m7 14 4-4 3 3 5-5"/></svg>
      <span class="ws-info"><b>${esc(ds.name)}</b><span>${plural(ds.metric_count,"metric")} · <span class="ws-dom">${DOMAIN_LABEL[ds.domain]||"FP&A"}</span></span></span>
      <span class="ws-kebab exec-hide" onclick="event.stopPropagation();toggleKebab(event,${ds.id})">
        <button aria-label="Dataset options">⋯</button>
        <div class="ws-kmenu" id="kmenu-${ds.id}">
          <button onclick="event.stopPropagation();renameDataset(${ds.id})">Rename</button>
          <button class="danger" onclick="event.stopPropagation();deleteDataset(${ds.id})">Delete</button>
        </div>
      </span>
    </div>`).join("")||'<div class="ws-item">No datasets</div>';
  $("ws-menu").innerHTML=rows+`<button class="ws-newbtn exec-hide" onclick="event.stopPropagation();$('ws-menu').classList.remove('open');nav('import')">+ Import new dataset</button>`;
}
function dsName(id){const d=(datasetsCache||[]).find(x=>x.id===id);return d?d.name:"this dataset";}
function toggleKebab(e,id){e.stopPropagation();
  document.querySelectorAll(".ws-kmenu").forEach(m=>{if(m.id!=="kmenu-"+id)m.classList.remove("open");});
  $("kmenu-"+id).classList.toggle("open");
}
async function renameDataset(id){
  const cur=dsName(id);
  const name=await modalInput("Rename dataset",{label:"Dataset name",value:cur,okLabel:"Rename",required:true});
  if(name==null||name===cur)return;
  try{await api(`/datasets/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})});toast("Renamed");await loadDatasets();}
  catch(e){toast(e.message);}
}
function toggleWs(e){e.stopPropagation();$("ws-menu").classList.toggle("open");}
document.addEventListener("click",()=>{$("ws-menu").classList.remove("open");$("wsp-menu")?.classList.remove("open");$("export-menu")?.classList.remove("open");document.querySelectorAll(".ws-kmenu").forEach(m=>m.classList.remove("open"));});
/* ---------- workspaces (v4.0 tenancy) ---------- */
async function loadWorkspaces(){
  if(!authToken){$("wsp-wrap").style.display="none";$("wsp-label").style.display="none";return;}
  let d;try{d=await api("/workspaces");}catch{return;}
  const list=d.workspaces||[];
  activeWorkspaceId=d.active_id!=null?d.active_id:((list[0]&&list[0].id)||null);
  try{if(activeWorkspaceId!=null)localStorage.setItem("cb-ws",String(activeWorkspaceId));}catch{}
  const active=list.find(w=>w.id===activeWorkspaceId)||list[0];
  $("wsp-name").textContent=active?active.name:"Workspace";
  $("wsp-wrap").style.display="";$("wsp-label").style.display="";
  $("wsp-menu").innerHTML=list.map(w=>`
    <button class="ws-item" onclick="switchWorkspace(${w.id})" style="width:100%">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="${w.id===activeWorkspaceId?'var(--accent)':'var(--mut2)'}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21h18M6 21V8l6-4 6 4v13"/></svg>
      <span class="ws-info"><b>${esc(w.name)}</b><span style="text-transform:capitalize">${esc(w.role)}</span></span>
    </button>`).join("")
    +`<button class="ws-newbtn" onclick="createWorkspaceUI()">+ New workspace</button>`;
}
function toggleWsp(e){e.stopPropagation();$("wsp-menu").classList.toggle("open");}
async function switchWorkspace(id){
  $("wsp-menu").classList.remove("open");
  if(id===activeWorkspaceId)return;
  activeWorkspaceId=id;try{localStorage.setItem("cb-ws",String(id));}catch{}
  toast("Switched workspace");
  await loadWorkspaces();await loadPeriods();
}
async function createWorkspaceUI(){
  const name=await modalInput("New workspace",{label:"Workspace name",placeholder:"Acme Finance",okLabel:"Create",required:true});
  if(!name)return;
  try{const r=await api("/workspaces",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})});
    activeWorkspaceId=r.id;try{localStorage.setItem("cb-ws",String(r.id));}catch{}
    toast("Workspace created");await loadWorkspaces();await loadPeriods();
  }catch(e){toast(e.message);}
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
  closeSidebar();$("statrow").innerHTML="";$("cards").innerHTML=skeletonCards();  // brief loading state
  try{await api(`/datasets/${id}/activate`,{method:"POST"});toast("Switched dataset");await loadPeriods();}
  catch(e){toast("Couldn't switch dataset: "+e.message);}
}
async function deleteDataset(id){
  const ok=await modalConfirm("Delete dataset",`Delete "${dsName(id)}" and all its data? This can't be undone.`,{okLabel:"Delete"});
  if(!ok)return;
  try{
    await api(`/datasets/${id}`,{method:"DELETE"});
    toast("Dataset deleted");
    await loadDatasets();
    await loadPeriods();
    const h=location.hash.slice(1);
    if(["digest","context","import"].includes(h))nav(h);
  }catch(e){toast("Delete failed: "+e.message);}
}

/* ---------- periods & facts ---------- */
let granularity="month";
function setGranularity(g){
  if(g===granularity)return;granularity=g;
  document.querySelectorAll("#gran-seg button").forEach(b=>b.classList.toggle("active",b.dataset.g===g));
  loadPeriods();
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
  if(granularity==="year")return periods.map(p=>`<option>${p}</option>`).join("");
  const byYear={};periods.forEach(p=>{const y=p.slice(0,4);(byYear[y]=byYear[y]||[]).push(p);});
  return Object.keys(byYear).sort().map(y=>`<optgroup label="${y}">${byYear[y].map(p=>`<option>${p}</option>`).join("")}</optgroup>`).join("");
}
async function loadPeriods(){
  await loadDatasets();
  const periods=await api(`/periods?granularity=${granularity}`);
  const sel=$("period-select");
  sel.innerHTML=granularity==="month"?periods.map(p=>`<option>${p}</option>`).join(""):groupPeriodsByYear(periods);
  if(periods.length){sel.value=periods[periods.length-1];currentPeriod=sel.value;await loadFacts();}
  else{
    // Empty workspace: show a centered import CTA on the dashboard, don't jump away.
    $("dash-sub").textContent="No data yet";$("statrow").innerHTML="";$("filter-pills").innerHTML="";$("cat-pills").innerHTML="";$("dash-title").textContent="Dashboard";
    $("cards").innerHTML=`<div class="empty-state" style="grid-column:1/-1;padding:70px 20px">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="width:52px;height:52px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/></svg>
      <div style="font-size:17px;font-weight:800;color:var(--ink);margin:14px 0 6px">Import your financials</div>
      <div style="max-width:34ch;margin:0 auto 18px">Drop in a CSV or Excel export — any layout. We detect your columns and draft variance commentary in minutes.</div>
      <button class="btn primary exec-hide" onclick="nav('import')" style="font-size:14px;padding:11px 20px">Import your first file</button>
    </div>`;
  }
}
function skeletonCards(n=6){
  const one=`<div class="sk-card"><div class="sk sk-line" style="width:45%"></div>
    <div class="sk" style="height:30px;width:60%"></div>
    <div style="display:flex;gap:8px"><div class="sk" style="height:22px;width:78px"></div><div class="sk" style="height:22px;width:88px"></div></div>
    <div class="sk sk-line" style="width:100%"></div><div class="sk sk-line" style="width:92%"></div><div class="sk sk-line" style="width:70%"></div></div>`;
  return Array(n).fill(one).join("");
}
async function loadFacts(){
  currentPeriod=$("period-select").value;syncStepButtons();
  $("dash-loading").style.display="none";$("statrow").innerHTML="";$("cards").innerHTML=skeletonCards();
  try{
    currentFacts=await api(`/facts?period=${encodeURIComponent(currentPeriod)}&granularity=${granularity}`);
    const cats=[...new Set(currentFacts.map(f=>f.category||"Uncategorized"))].sort();
    assignCatSlots(cats);
    renderCatPills(cats);
    renderCards();
    const anom=currentFacts.filter(f=>f.is_anomaly).map(f=>f.metric);
    $("dash-title").textContent=fmtPeriodTitle(currentPeriod);
    const aggNote=granularity==="month"?"":" · narratives are written monthly";
    $("dash-sub").innerHTML=`${currentFacts.length} ${currentFacts.length===1?"KPI":"KPIs"} · <b>${anom.length?plural(anom.length,"anomaly","anomalies"):"no anomalies"}</b>${aggNote}`;
    const anomKey=`anomaly:${currentPeriod}`;
    if(anom.length&&!bannerDismissed(anomKey)){
      $("anomaly-banner").style.display="";
      $("anomaly-banner").innerHTML=warnIcon()+`<span>Anomalous movement: ${anom.map(esc).join(", ")}. Open a card for the full story.</span>`+dismissX(anomKey,"anomaly-banner");
    }else $("anomaly-banner").style.display="none";
    updateGenButton();maybePromptGenerate();setFreshness();
    loadConflicts();renderFunnel();
  }catch(e){
    $("statrow").innerHTML="";$("dash-sub").textContent="Couldn't load data";
    $("cards").innerHTML=`<div class="empty-state" style="grid-column:1/-1;padding:56px 20px">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:40px;height:40px"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
      <div style="margin:12px 0 4px;font-weight:700;color:var(--ink2)">Couldn't load KPIs</div>
      <div style="font-size:12.5px;margin-bottom:16px">${esc(e.message||"Something went wrong.")}</div>
      <button class="btn primary" onclick="loadFacts()">Retry</button></div>`;
  }finally{$("dash-loading").style.display="none";refreshReviewBadge();}
}
function setFreshness(){
  const el=$("dash-fresh");if(!el)return;
  const sel=$("period-select"),latest=sel&&sel.options.length?sel.options[sel.options.length-1].value:currentPeriod;
  const imported=activeDatasetObj&&activeDatasetObj.created_at?activeDatasetObj.created_at.slice(0,10):null;
  const narr=currentFacts.filter(f=>f.has_data&&f.narrative).length,tot=currentFacts.filter(f=>f.has_data).length;
  const bits=[`Data through ${esc(latest||"—")}`];
  if(imported)bits.push(`imported ${esc(imported)}`);
  if(tot)bits.push(narr===tot?"narratives current":`${narr}/${tot} narratives generated`);
  el.textContent=bits.join(" · ");
}
function maybePromptGenerate(){
  // Offer (never force) generation when a month period has data but no narratives.
  const el=$("import-banner");if(!el||demoSession||granularity!=="month")return;
  const withData=currentFacts.filter(f=>f.has_data),none=withData.length&&withData.every(f=>!f.narrative);
  if(!none){if(el.classList.contains("info"))el.style.display="none";return;}
  if(el.style.display==="none"){
    el.style.display="";el.className="banner info";
    el.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h7l-1 8 10-12h-7z"/></svg>
      <span>No narratives for ${esc(currentPeriod)} yet.</span>
      <button class="btn sm primary exec-hide" style="margin-left:auto" onclick="$('import-banner').style.display='none';generateAll()">Generate narratives</button>
      <button class="ico" style="border:none" onclick="$('import-banner').style.display='none'" aria-label="Dismiss">✕</button>`;
  }
}
function dismissKey(type){return `cb-dismiss:${(currentUser&&currentUser.email)||"anon"}:${activeDatasetId}:${type}`;}
function bannerDismissed(type){try{return localStorage.getItem(dismissKey(type))==="1";}catch{return false;}}
function dismissX(type,elId){return `<button class="ico" style="border:none;margin-left:auto" aria-label="Dismiss" onclick="try{localStorage.setItem('${dismissKey(type)}','1')}catch{};$('${elId}').style.display='none'">✕</button>`;}
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
      <div class="fh-sub">${leakLine} ${activeDomain.narrative_style?'· <button class="txtact acc" onclick="explainFunnel(this)">Explain funnel</button>':""}</div>
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
const TICK='<svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
const seal=ok=>ok
  ?`<span class="seal" title="Every figure checked against computed facts">${TICK}Verified</span>`
  :`<span class="seal failed" title="A figure couldn't be verified — review before sharing"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>Needs review</span>`;
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
const usd=v=>{v=+v||0;return "$"+(v<1?v.toFixed(4):v.toFixed(2));};
let costsData=null;
async function loadCosts(){
  $("costs-loading").style.display="";
  try{
    const c=await api("/costs");costsData=c;
    // Stat tiles
    const cache=c.cache||{},hr=cache.hit_rate;
    const lat=c.latency||{},p95s=Object.values(lat).map(x=>x.p95_ms).filter(v=>v!=null);
    const worstP95=p95s.length?Math.max(...p95s):null;
    const al=c.alert_latency||{};
    const svg=p=>`<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
    const tile=(lbl,path,col,val,hint)=>`<div class="stat rise" style="--sc:${col}"><span class="stat-lbl">${svg(path)}${lbl}</span><span class="stat-val nbi-num">${val}</span><span class="stat-hint">${hint}</span></div>`;
    $("costs-stats").innerHTML=[
      tile("Total spend",'<path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',"#008300",usd(c.total_cost_usd),"across all LLM calls"),
      tile("LLM calls",'<path d="M13 2 3 14h7l-1 8 10-12h-7z"/>',"#4a3aa7",c.total_llm_calls||0,"generations + digests"),
      tile("Cache hit rate",'<path d="M21 12a9 9 0 1 1-6.2-8.5"/><path d="M21 3v6h-6"/>',hr>=0.5?"var(--green)":"#eda100",hr==null?"—":Math.round(hr*100)+"%",`${cache.hits||0} hits · ${cache.misses||0} misses`),
      tile("Worst p95 latency",'<path d="M12 8v4l3 2"/><circle cx="12" cy="12" r="9"/>',worstP95>2000?"var(--red)":"#2a78d6",worstP95==null?"—":Math.round(worstP95)+" ms","slowest endpoint"),
      tile("Alert latency p95",'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',al.within_30s===false?"var(--red)":"var(--green)",al.p95_ms==null?"—":(al.p95_ms/1000).toFixed(1)+" s",al.n?`${al.n} alerts · target < 30s`:"no alerts yet"),
    ].join("");
    // Spend-by-day chart (aggregate endpoints per day)
    const byDay={};(c.by_day||[]).forEach(r=>{byDay[r.day]=(byDay[r.day]||0)+(+r.cost_usd||0);});
    const days=Object.keys(byDay).sort();
    ensureCanvas("c-spend");const box=$("box-c-spend");
    if(!days.length){if(chartReg["c-spend"]){chartReg["c-spend"].destroy();delete chartReg["c-spend"];}box.innerHTML='<div class="loading">No LLM spend recorded yet.</div>';}
    else{const th=chartTheme();
      mkChart("c-spend",{type:"bar",
        data:{labels:days.map(d=>d.slice(5)),datasets:[{label:"Spend",data:days.map(d=>byDay[d]),backgroundColor:th.accent,borderRadius:5,borderSkipped:false,maxBarThickness:40}]},
        options:{responsive:true,maintainAspectRatio:false,
          plugins:{legend:{display:false},tooltip:tooltipCfg("USD",{label:c=>" "+usd(c.parsed.y)})},
          scales:{y:{grid:{color:th.grid},ticks:{color:th.mut,maxTicksLimit:5,callback:v=>usd(v)},border:{display:false}},
            x:{grid:{display:false},ticks:{color:th.mut,maxRotation:0,autoSkip:true,maxTicksLimit:8},border:{color:th.grid}}}}});
    }
    // Latency table
    const lrows=Object.entries(lat);
    $("costs-latency").innerHTML=lrows.length?`<table class="ctx-table" style="margin-top:8px"><thead><tr><th>Endpoint</th><th style="text-align:right">p50</th><th style="text-align:right">p95</th><th style="text-align:right">n</th></tr></thead><tbody>${lrows.map(([k,v])=>`<tr><td style="font-family:ui-monospace,monospace;font-size:12px">${esc(k)}</td><td style="text-align:right" class="nbi-num">${Math.round(v.p50_ms)} ms</td><td style="text-align:right" class="nbi-num">${Math.round(v.p95_ms)} ms</td><td style="text-align:right" class="nbi-num">${v.n}</td></tr>`).join("")}</tbody></table>`:'<div class="loading">No latency samples yet this session.</div>';
    // Endpoint spend table
    const rows=c.by_day||[];
    $("costs-table").innerHTML=rows.length?`<table class="ctx-table"><thead><tr><th>Day</th><th>Endpoint</th><th style="text-align:right">Calls</th><th style="text-align:right">Prompt tok</th><th style="text-align:right">Completion tok</th><th style="text-align:right">Cost</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.day)}</td><td style="font-family:ui-monospace,monospace;font-size:12px">${esc(r.endpoint)}</td><td style="text-align:right" class="nbi-num">${r.calls}</td><td style="text-align:right" class="nbi-num">${r.prompt_tokens||0}</td><td style="text-align:right" class="nbi-num">${r.completion_tokens||0}</td><td style="text-align:right" class="nbi-num">${usd(r.cost_usd)}</td></tr>`).join("")}</tbody></table>`:'<div class="loading">No spend recorded yet.</div>';
  }catch(e){toast(e.message||"Could not load costs");}
  finally{$("costs-loading").style.display="none";}
}

function deltaChip(label,v,downGood){
  const p=pct(v);if(p===null)return`<span class="delta flat">${label} –</span>`;
  const good=downGood?v<0:v>0,cls=v===0?"flat":good?"good":"bad";
  const arrow=v>0?"▲":v<0?"▼":"■";
  return`<span class="delta ${cls}">${arrow} ${label} ${p}</span>`;
}
function narrativeBlock(f,q,i){
  if(generatingSet.has(f.metric))
    return `<div class="narr"><div class="sk sk-line" style="width:100%;margin-bottom:5px"></div><div class="sk sk-line" style="width:94%;margin-bottom:5px"></div><div class="sk sk-line" style="width:72%"></div></div>`;
  if(f.aggregated)return "";   // one note in the page sub-line, not repeated per card
  let html=`<div class="narr ${f.narrative?"":"muted"}">${f.narrative?hl(f.narrative,q):"No narrative yet — generate one to explain this movement."}</div>`;
  if(f.faithfulness==="failed")
    // The guard caught an unverifiable number. Surface it — do NOT hide it.
    html+=`<div class="banner err" style="margin:8px 0 0;font-size:11.5px;padding:8px 10px">${warnIcon()}<span>Numbers couldn't be verified — review manually.</span><button class="btn sm exec-hide" style="margin-left:auto;padding:3px 9px" onclick="event.stopPropagation();retryCard(${i})">Retry</button></div>`;
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
  return`<span class="delta ${up?"good":"bad"}" title="${esc(s.start_period)} → ${esc(s.end_period)}">${up?"↗":"↘"} ${s.months}-mo ${up?"streak":"decline"}</span>`;
}

function renderStatrow(facts){
  const el=$("statrow");if(!el)return;
  const withData=facts.filter(f=>f.has_data);
  if(!withData.length){el.innerHTML="";return;}
  const anom=withData.filter(f=>f.is_anomaly).length;
  const narr=withData.filter(f=>f.narrative).length;
  const offPlan=withData.filter(f=>f.deltas&&f.deltas.budget_var_pct!=null&&f.deltas.budget_var_pct<0).length;
  // Biggest absolute move vs plan, for a "top mover" headline tile.
  const mover=withData.filter(f=>f.deltas&&f.deltas.budget_var_pct!=null)
    .sort((a,b)=>Math.abs(b.deltas.budget_var_pct)-Math.abs(a.deltas.budget_var_pct))[0];
  const ic={grid:'<path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"/>',
    bolt:'<path d="M13 2 3 14h7l-1 8 10-12h-7z"/>',
    warn:'<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    trend:'<path d="M3 3v18h18"/><path d="m7 14 4-4 3 3 5-5"/>'};
  // The svg inherits --sc from its .stat container — set the color once, on the tile.
  const svg=p=>`<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
  const tile=(lbl,path,color,val,hint,mono)=>`<div class="stat rise" style="--sc:${color}">
    <span class="stat-lbl">${svg(path)}${lbl}</span>
    <span class="stat-val${mono?" nbi-num":""}">${val}</span><span class="stat-hint">${hint}</span></div>`;
  const attn=anom?`${plural(anom,"anomaly","anomalies")}`:"All clear";
  el.innerHTML=[
    `<div class="stat rise${anom?" alert":""}" style="--sc:${anom?"var(--red)":"var(--accent)"}"><span class="stat-lbl">${svg(ic.warn)}Needs attention</span><span class="stat-val">${attn}</span><span class="stat-hint">${anom?"open the flagged card to review":"no anomalies this period"}</span></div>`,
    mover?tile("Top mover vs plan",ic.trend,catColor(mover.category)||"#eb6834",esc(mover.metric),`${pct(mover.deltas.budget_var_pct)||"–"} vs budget`):"",
    tile("Narratives",ic.bolt,"#4a3aa7",`${narr}<small>/${withData.length}</small>`,narr===withData.length?"all explained":`${withData.length-narr} to generate`,true),
    tile("Off plan",ic.grid,offPlan?"var(--amber)":"#2a78d6",offPlan,offPlan?"KPIs behind budget":"everything on or ahead of plan",true)
  ].join("");
}
let shownFacts=[];
function renderPills(){
  const cat=catFilter,pills=[];
  if(cat)pills.push(`Category: ${esc(cat)}<button onclick="clearCatFilter()" aria-label="Clear category filter">×</button>`);
  if(searchQuery)pills.push(`Search: “${esc(searchQuery)}”<button onclick="clearSearch()" aria-label="Clear search">×</button>`);
  $("filter-pills").innerHTML=pills.map(p=>`<span class="fpill">${p}</span>`).join("");
}
function clearCatFilter(){setCat("");}
function clearSearch(){searchQuery="";const s=$("search");if(s)s.value="";renderCards();}
function renderCards(){
  const cat=catFilter;
  let facts=cat?currentFacts.filter(f=>(f.category||"Uncategorized")===cat):currentFacts;
  if(searchQuery)facts=facts.filter(f=>factMatches(f,searchQuery));
  shownFacts=facts;
  renderStatrow(facts);renderPills();
  if(!currentFacts.length){$("cards").innerHTML=`<div class="empty-state" style="grid-column:1/-1"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/></svg><div>No KPIs to show. Select KPIs during import, or switch dataset.</div></div>`;return;}
  if(!facts.length){$("cards").innerHTML=`<div class="empty-state" style="grid-column:1/-1"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg><div>No metrics match ${searchQuery?`“${esc(searchQuery)}”`:"these filters"}. <a onclick="clearSearch();clearCatFilter()" style="color:var(--accent);cursor:pointer;font-weight:700">Clear filters</a></div></div>`;return;}
  const q=searchQuery;
  // Only run the staggered entrance on the first paint of a dataset/period —
  // not on every re-render during generation (which caused a full-grid flicker).
  const rise=skipRise?"":"rise";
  $("cards").innerHTML=facts.map((f,i)=>{
    const dg=f.direction_good==="down",delay=`style="${skipRise?"":`animation-delay:${Math.min(i*45,400)}ms;`}${catStyle(f.category)}"`;
    if(!f.has_data){
      return`<div class="card empty ${rise}" ${delay}>
        <div class="card-top"><span class="card-name"><span class="cat-dot"></span>${hl(f.metric,q)}</span></div>
        <div class="card-value" style="font-size:20px;color:var(--mut2)">No data</div>
        <div class="narr muted">No data for ${esc(currentPeriod)} in this dataset.</div></div>`;
    }
    // ONE status slot beside the value (was 4 competing badge systems).
    const status=f.is_anomaly?'<span class="anom">Anomaly</span>'
      :f.faithfulness==="failed"?seal(false)
      :f.faithfulness==="passed"?seal(true):"";
    const nsrc=(f.sources||[]).length;
    return`<div class="card click ${rise}" ${delay} onclick="openDetail(${i})">
      <div class="card-top">
        <span class="card-name" title="${esc(f.category||"Uncategorized")}"><span class="cat-dot"></span>${hl(f.metric,q)}</span>
        ${f.chart_data&&f.chart_data.trend?sparkline(f.chart_data.trend,f.direction_good,96,28,f.unit):""}
      </div>
      <div class="card-value-row">
        <span style="display:flex;align-items:center;gap:9px"><span class="card-value nbi-num" ${f.unavailable?'title="Ratios/percentages aren\'t aggregated across months — view at Month granularity"':""}>${f.unavailable?"—":fmt(f.value,f.unit)}</span>${status}${f.edited?'<span class="badge edited">edited</span>':""}</span>
      </div>
      <div class="deltas">
        ${deltaChip(f.aggregated?(granularity==="year"?"YoY":"QoQ"):"MoM",f.deltas.mom_pct,dg)}
        ${deltaChip("vs plan",f.deltas.budget_var_pct,dg)}
        ${streakBadge(f.trend_streak)}
      </div>
      ${narrativeBlock(f,q,i)}
      ${f.narrative&&!f.aggregated?`<span class="read-more">Read more${nsrc?` · ${plural(nsrc,"source")}`:""}</span>`:""}
      ${f.aggregated?"":`<div class="card-foot" onclick="event.stopPropagation()">
        ${f.report_id?`
          <button class="txtact" onclick="feedback(${f.report_id},'accepted',this)">Accept</button>
          <button class="txtact" onclick="editNarr(${f.report_id})">Edit</button>
          <button class="txtact bad" onclick="rejectNarr(${f.report_id},this)">Reject</button>`:""}
        <button class="txtact acc" style="margin-left:auto" onclick="genCard(${i},this)">${f.narrative?"Regenerate":"Generate"}</button>
      </div>`}
    </div>`;
  }).join("");
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
  Chart.defaults.font.family="'Plus Jakarta Sans',system-ui,-apple-system,sans-serif";
  Chart.defaults.font.size=11;
  Chart.defaults.animation.duration=600;
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
  ensureCanvas("c-trend");const box=$("box-c-trend");
  const has=trend.some(t=>t.value!=null||t.budget!=null);
  if(!has){if(chartReg["c-trend"]){chartReg["c-trend"].destroy();delete chartReg["c-trend"];}box.innerHTML='<div class="loading">No trend data.</div>';return;}
  const th=chartTheme();
  mkChart("c-trend",{type:"line",
    data:{labels:trend.map(t=>t.period.slice(2)),datasets:[
      {label:"Actual",data:trend.map(t=>t.value),borderColor:th.accent,borderWidth:2,tension:.35,
        pointRadius:0,pointHoverRadius:4,pointBackgroundColor:th.accent,spanGaps:true,
        fill:true,backgroundColor:ctx=>{const{chartArea,ctx:c}=ctx.chart;if(!chartArea)return"transparent";
          const g=c.createLinearGradient(0,chartArea.top,0,chartArea.bottom);
          g.addColorStop(0,th.accent+"33");g.addColorStop(1,th.accent+"03");return g;}},
      {label:"Budget",data:trend.map(t=>t.budget),borderColor:th.mut,borderWidth:2,borderDash:[5,4],
        tension:.35,pointRadius:0,pointHoverRadius:3,fill:false,spanGaps:true}]},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      plugins:{legend:{display:false},tooltip:tooltipCfg(unit)},
      scales:{y:{grid:{color:th.grid},ticks:{color:th.mut,maxTicksLimit:5,callback:v=>fmt(v,unit)},border:{display:false}},
        x:{grid:{display:false},ticks:{color:th.mut,maxRotation:0,autoSkip:true,maxTicksLimit:7},border:{color:th.grid}}}}});
}
function renderBvaChart(bva,unit){
  ensureCanvas("c-bva");const box=$("box-c-bva");
  if(!bva||bva.actual==null){if(chartReg["c-bva"]){chartReg["c-bva"].destroy();delete chartReg["c-bva"];}box.innerHTML='<div class="loading">No data.</div>';return;}
  const th=chartTheme();
  mkChart("c-bva",{type:"bar",
    data:{labels:["Actual","Budget"],datasets:[{label:"",data:[bva.actual,bva.budget],
      backgroundColor:[th.accent,th.mut],borderRadius:5,borderSkipped:false,barThickness:22}]},
    options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:tooltipCfg(unit,{label:c=>` ${c.label}: ${fmt(c.parsed.x,unit)}`})},
      scales:{x:{grid:{color:th.grid},ticks:{color:th.mut,maxTicksLimit:5,callback:v=>fmt(v,unit)},border:{display:false}},
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
function trendTable(trend,unit){if(!trend.length)return"";return`<details class="tbl"><summary>Show data table</summary><table><thead><tr><th>Period</th><th>Actual</th><th>Budget</th></tr></thead><tbody>${trend.map(t=>`<tr><td>${esc(t.period)}</td><td>${fmt(t.value,unit)}</td><td>${fmt(t.budget,unit)}</td></tr>`).join("")}</tbody></table></details>`;}

/* ---------- metric detail ---------- */
function openDetail(i){
  const f=shownFacts[i];if(!f||!f.has_data)return;detailFact=f;nav("detail");
  $("d-title").textContent=f.metric;const dg=f.direction_good==="down";
  $("d-sub").innerHTML=`<span class="cat-dot" style="${catStyle(f.category)}"></span>${esc(f.category||"")} · ${esc(currentPeriod)} · ${fmt(f.value,f.unit)} `
    +deltaChip("MoM",f.deltas.mom_pct,dg)+" "+deltaChip("vs plan",f.deltas.budget_var_pct,dg)
    +(f.is_anomaly?' <span class="anom">Anomaly</span>':"");
  const cd=f.chart_data||{};
  // Restore canvases (an earlier "no data" state may have replaced them with text).
  $("d-trend").innerHTML=trendTable(cd.trend||[],f.unit);
  ensureCanvas("c-trend");ensureCanvas("c-bva");ensureCanvas("c-bridge");
  renderTrendChart(cd.trend||[],f.unit);
  $("d-bva-sub").textContent=f.deltas.budget_var_abs!=null?`${fmt(f.deltas.budget_var_abs,f.unit)} vs budget (${pct(f.deltas.budget_var_pct)||"–"})`:"";
  renderBvaChart(cd.budget_vs_actual,f.unit);
  renderBridgeChart(cd.variance_bridge,f.deltas.budget_var_abs,f.unit);
  $("d-narr").className="narr"+(f.narrative?"":" muted");
  renderReviewStrip(f);
  {const rc=$("d-rootcause");if(rc){rc.style.display="none";rc.innerHTML="";}}
  $("d-grounding").style.display="none";groundingData=null;
  if(f.narrative&&f.report_id&&!f.edited){
    // Drill-down: fetch per-sentence grounding and render clickable sentences.
    renderGroundedNarrative(f.report_id);
  }else{
    $("d-narr").innerHTML=f.narrative?esc(f.narrative)+(f.edited&&f.original_narrative?`<details class="tbl" style="margin-top:8px"><summary>Show original (pre-edit)</summary><p style="margin-top:6px;color:var(--mut)">${esc(f.original_narrative)}</p></details>`:""):"No narrative yet — generate one from the dashboard card.";
  }
  $("d-narr-meta").textContent=(f.confidence?`${f.confidence} confidence · numbers ${f.faithfulness}`:"")+(f.edited?" · edited by analyst":"")+(f.cost_usd!=null?` · ≈ $${(+f.cost_usd).toFixed(4)}`:"");
  $("d-chips").innerHTML=(f.sources||[]).map(s=>`<span class="chip" title="Open source note" onclick="showSourceDoc('${esc(s.id)}')">${DOCICON}${esc(s.title||s.id)}</span>`).join("");
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
  el.innerHTML=sents.map(s=>`<span class="gsent ${s.kind}" data-i="${s.idx}" title="Click to see what grounds this sentence" onclick="showSentenceGrounding(${s.idx})">${esc(s.text)}</span>`).join(" ")
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
    const chips=s.context_ids.map(id=>`<span class="chip" onclick="showSourceDoc('${esc(id)}')">${DOCICON}${esc(id)}</span>`).join("");
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
    const srcs=(t.sources||[]).map(s=>`<span class="chip" onclick="showSourceDoc('${esc(s.id)}')">${DOCICON}${esc(s.title||s.id)}</span>`).join("");
    return `<div class="qa-turn">
      <div class="qa-q">${esc(t.question)}</div>
      <div class="qa-a"><div class="narr" style="font-size:14.5px">${esc(t.answer)}</div>
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
    toast(r.faithfulness==="failed"?`${metric}: numbers need review`:(r.cached?"Served from cache — no LLM cost":`Narrative written for ${metric}`));
  }catch(e){generatingSet.delete(metric);renderCards();toast("Failed: "+errMsg(e));}
}
// Rough per-narrative cost estimate (gpt-4o-class: ~1.5k prompt + ~180 completion tokens).
const EST_COST_PER_NARR=0.006;
let generatingSet=new Set(), skipRise=false;
// Simple concurrency pool: run `worker` over `items`, at most `n` in flight.
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
  if(!n){btn.innerHTML=`Regenerate all<small>narratives are current</small>`;return;}
  btn.innerHTML=`Generate ${plural(n,"narrative")}<small>≈ $${(n*EST_COST_PER_NARR).toFixed(2)} · ~${n*7}s</small>`;
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
  let done=0;
  await runPool(list,3,async(f)=>{
    try{
      const r=await api("/generate-insight",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({metric:f.metric,period:gp,force})});
      if(currentPeriod!==gp||granularity!==gg||activeDatasetId!==gd)return;   // user moved on — drop the result
      applyGenerated(r);
    }catch(e){/* leave card un-narrated; user can retry */}
    if(currentPeriod!==gp||granularity!==gg||activeDatasetId!==gd)return;
    generatingSet.delete(f.metric);done++;
    btn.innerHTML=`<span class="spin"></span> ${done} of ${total}`;
    renderCards();
  });
  skipRise=false;btn.disabled=false;generatingSet.clear();updateGenButton();
  if(currentPeriod!==gp)return;
  const failed=currentFacts.filter(f=>f.faithfulness==="failed").length;
  toast(failed?`Done — ${plural(failed,"narrative")} need review`:"All narratives generated ✓");
}
async function feedback(id,action,btn,extra){
  try{
    await api("/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({report_id:id,action,...extra})});
    if(btn)btn.classList.add(action==="accepted"?"on":"on-bad");
    toast(action==="accepted"?"Accepted":action==="rejected"?"Rejected":"Edit saved");
  }catch(e){toast("Couldn't save feedback: "+e.message);}
}
async function rejectNarr(id,btn){
  const r=await modalInput("Reject narrative",{label:"Why is this wrong? (optional)",textarea:true,placeholder:"e.g. attributes the drop to the wrong cause",okLabel:"Reject"});
  if(r===null)return;   // cancelled
  feedback(id,"rejected",btn,{reason:r||null});
}
async function editNarr(id){
  const f=currentFacts.find(x=>x.report_id===id);
  const cur=(f&&f.narrative)||"";
  const t=await modalInput(`Edit narrative${f?" — "+f.metric:""}`,{label:"Narrative",value:cur,textarea:true,okLabel:"Save"});
  if(t==null||t===cur)return;
  await feedback(id,"edited",null,{edited_text:t});loadFacts();
}

/* ---------- digest ---------- */
async function loadDigest(force){
  $("digest-loading").style.display="";$("digest-list").innerHTML="";
  try{
    // The digest is written monthly; at quarter/year granularity use the latest month.
    let dp=currentPeriod;
    if(granularity!=="month"){const months=await api("/periods?granularity=month");dp=months[months.length-1]||currentPeriod;}
    const d=await api(`/digest?period=${encodeURIComponent(dp)}&top_n=5&force=${!!force}`,{method:"POST"});
    $("digest-list").innerHTML=d.items.map((it,i)=>`<div class="digest-item">
      <span class="rank"><span class="rank-n${i<3?" top":""}">#${i+1}</span>${esc(it.metric)}</span><h3>${esc(it.headline)}</h3>
      ${it.detail?`<p>${esc(it.detail)}</p>`:""}
      <div style="margin-top:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span class="delta ${it.budget_var_abs<0?"bad":"good"}">${fmt(it.budget_var_abs,"USD")} vs budget (${pct(it.budget_var_pct)||"–"})</span>
        ${seal(it.faithfulness==="passed")}
        ${it.report_id?`<button class="txtact" style="margin-left:auto" onclick="feedback(${it.report_id},'accepted',this)">Accept</button><button class="txtact bad" onclick="rejectNarr(${it.report_id},this)">Reject</button>`:""}
      </div></div>`).join("")||'<div class="empty-state">No movers with budget data for this period.</div>';
    if(d.cost_usd!=null||d.cached)$("digest-list").insertAdjacentHTML("beforeend",`<div class="cost" style="margin-top:8px">${d.cached?"served from cache (no LLM cost)":"≈ $"+(+d.cost_usd).toFixed(4)+" this run"}</div>`);
  }catch(e){toast("Digest failed: "+e.message);}finally{$("digest-loading").style.display="none";loadDigestHistory();}
}
// Period-over-period: past digests (manual + scheduled), newest first (Phase 4).
async function loadDigestHistory(){
  const el=$("digest-history");if(!el)return;
  let runs;try{runs=await api("/digests/history");}catch{el.innerHTML="";return;}
  if(!runs||runs.length<2){el.innerHTML="";return;}   // need history to compare
  el.innerHTML=`<h3 style="font-family:var(--serif);font-size:17px;font-weight:600;margin-bottom:10px">Digest history</h3>`
    +runs.map(r=>{
      const when=r.created_at?String(r.created_at).slice(0,10):"";
      const top=(r.items||[]).slice(0,3).map(it=>esc(it.metric)).join(", ");
      return `<div class="digest-item" style="padding:13px 18px">
        <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
          <b style="font-size:14px">${esc(r.period)}</b>
          <span class="fh-sub" style="font-size:11.5px;color:var(--mut2)">${esc(r.trigger)} · ${esc(when)}${r.cost_usd!=null?" · ≈ $"+(+r.cost_usd).toFixed(4):""}</span>
        </div>
        ${top?`<div style="font-size:12.5px;color:var(--mut);margin-top:4px">Top movers: ${top}</div>`:""}
      </div>`;
    }).join("");
}

/* ---------- context ---------- */
async function loadContext(){
  let docs;try{docs=await api("/context");}catch(e){$("ctx-rows").innerHTML=`<tr><td colspan="5" class="loading">Couldn't load context — <a onclick="loadContext()" style="color:var(--accent);cursor:pointer;font-weight:700">retry</a></td></tr>`;return;}
  $("ctx-rows").innerHTML=docs.map(d=>`<tr>
    <td><span class="ctx-type">${esc(d.type.replace("_"," "))}</span></td>
    <td><div style="font-weight:700;font-size:13px;margin-bottom:3px">${esc(d.title)}</div><div style="color:var(--mut);line-height:1.45">${esc(d.body)}</div></td>
    <td>${d.metric_tags.length?d.metric_tags.map(t=>`<span class="ctx-tag">${esc(t)}</span>`).join(""):'<span style="color:var(--mut2)">all</span>'}</td>
    <td style="color:var(--mut);white-space:nowrap">${d.effective_date?esc(d.effective_date):"—"}</td>
    <td><button class="row-del exec-hide" onclick="delContext(${d.id})">✕</button></td></tr>`).join("")
    ||`<tr><td colspan="5" class="loading">Library is empty. Add your first context document above.</td></tr>`;
}
async function addContext(e){
  e.preventDefault();
  const tags=$("c-tags").value.split(",").map(s=>s.trim()).filter(Boolean);
  await api("/context",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({type:$("c-type").value,title:$("c-title").value,body:$("c-body").value,metric_tags:tags,effective_date:$("c-date").value||null})});
  toast("Added to Context Library");["c-title","c-body","c-tags","c-date"].forEach(id=>$(id).value="");loadContext();
}
async function delContext(id){
  if(!await modalConfirm("Delete context note","Delete this context document? Narratives will no longer cite it.",{okLabel:"Delete"}))return;
  try{await api(`/context/${id}`,{method:"DELETE"});toast("Deleted");loadContext();}catch(e){toast(e.message);}
}

/* ---------- import ---------- */
let upload=null,profile=null,lastImport=null;
function setStep(n){for(let i=1;i<=3;i++)$("st-"+i).className="step"+(i<n?" done":i===n?" active":"");
  $("imp-upload").style.display=n===1?"":"none";$("imp-map").style.display=n===2?"":"none";$("imp-kpis").style.display=n===3?"":"none";}
function dropFile(e){e.preventDefault();$("drop").classList.remove("drag");const f=e.dataTransfer.files[0];if(f)uploadFile(f);}
async function uploadFile(file){
  const fd=new FormData();fd.append("file",file);
  $("imp-result").innerHTML='<div class="loading"><span class="spin"></span> Uploading &amp; profiling…</div>';
  try{
    upload=await api("/ingest/upload",{method:"POST",body:fd});$("imp-result").innerHTML="";
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
  $("layout-sel").value=profile.layout_guess;renderMap();
}
const ROLES=["period","metric_label","measure","budget","quantity","price","dimension","ignore"];
function renderMap(){
  if(!profile)return;const layout=$("layout-sel").value;
  $("map-holder").innerHTML=`<table class="map"><thead><tr><th>Column</th><th>Type</th><th>Sample</th><th>Role</th></tr></thead><tbody>`+
    profile.columns.map(c=>{const wp=profile.wide_period_cols.includes(c.column_name);
      return`<tr><td><b>${esc(c.column_name)}</b>${layout==="wide"&&wp?' <span class="suggest">period col</span>':""}</td>
        <td>${esc(c.dtype)}</td><td class="samp">${c.sample_values.slice(0,3).map(esc).join(" · ")}</td>
        <td>${layout==="wide"&&wp?'<span class="samp">melted</span>':`<select data-col="${esc(c.column_name)}" class="role">${ROLES.map(r=>`<option value="${r}" ${c.guessed_role===r?"selected":""}>${r.replace("_"," ")}</option>`).join("")}</select>`}</td></tr>`;
    }).join("")+`</tbody></table>`;
}
function buildMapping(){
  const layout=$("layout-sel").value,roleOf={};
  document.querySelectorAll(".role").forEach(s=>roleOf[s.dataset.col]=s.value);
  const by=r=>Object.keys(roleOf).filter(c=>roleOf[c]===r);
  if(layout==="wide")return{layout:"wide",wide_period_cols:profile.wide_period_cols,wide_metric_col:by("metric_label")[0]||null,wide_value_label:by("metric_label").length?null:"Value",dimension_cols:by("dimension")};
  return{layout:"long",period_col:by("period")[0]||null,metric_col:by("metric_label")[0]||null,value_col:by("measure")[0]||null,budget_col:by("budget")[0]||null,quantity_col:by("quantity")[0]||null,price_col:by("price")[0]||null,dimension_cols:by("dimension")};
}
async function confirmMapping(m){
  const mapping=m||buildMapping();
  $("imp-result").innerHTML='<div class="loading"><span class="spin"></span> Normalizing &amp; computing…</div>';
  try{
    const res=await api(`/ingest/${upload.upload_id}/mapping?save_template=${$("save-tpl").checked}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(mapping)});
    $("imp-result").innerHTML="";
    $("norm-summary").innerHTML=`✓ Normalized ${plural(res.rows_normalized,"row")} · ${plural(res.metrics.length,"metric")} · ${plural(res.periods.length,"period")} · ${res.facts_computed} facts${res.template_id?" · template saved":""}`;
    lastImport={rows:res.rows_normalized,metrics:res.metrics.length,periods:res.periods.length};
    // Default the dataset name to the uploaded filename without its extension.
    const fn=(upload&&upload.filename)||"";
    $("imp-name").value=fn.replace(/\.[^.]+$/,"")||"Imported dataset";
    setStep(3);await loadKpiPicker();
  }catch(e){$("imp-result").innerHTML=`<div class="banner err">${warnIcon()}${esc(e.message)}</div>`;}
}
async function applyTpl(id){const t=await api(`/templates/${id}`);await confirmMapping(t.mapping);toast("Saved mapping applied");}
async function loadKpiPicker(){
  const metrics=await api(`/ingest/${upload.upload_id}/metrics`);
  $("kpi-rows").innerHTML=`<div class="kpi-grid hdr"><span>Source metric</span><span>Display name</span><span>Category</span><span>Unit</span><span>Direction</span></div>`+
    metrics.map(m=>{const s=m.suggestion||{};return`<div class="kpi-grid" data-src="${esc(m.source_metric)}">
      <span><b>${esc(m.source_metric)}</b>${s.name?`<br><span class="suggest">→ ${esc(s.name)}</span>`:""}</span>
      <input class="k-name" value="${esc(m.source_metric)}">
      <input class="k-cat" value="${esc(s.category||"Uncategorized")}">
      <select class="k-unit">${["USD","%","count"].map(u=>`<option ${u===(s.unit||"USD")?"selected":""}>${u}</option>`).join("")}</select>
      <select class="k-dir"><option value="up" ${(s.direction_good||"up")==="up"?"selected":""}>↑ good</option><option value="down" ${s.direction_good==="down"?"selected":""}>↓ good</option></select></div>`;}).join("");
}
async function confirmKpis(){
  const kpis=[...document.querySelectorAll(".kpi-grid[data-src]")].map(r=>({source_metric:r.dataset.src,display_name:r.querySelector(".k-name").value,category:r.querySelector(".k-cat").value,unit:r.querySelector(".k-unit").value,direction_good:r.querySelector(".k-dir").value}));
  try{
  await api("/kpis",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kpis})});
  // Apply the chosen display name + domain to the freshly-created dataset.
  const name=($("imp-name").value||"").trim(),domain=$("imp-domain").value;
  if(activeDatasetId!=null&&(name||domain)){
    try{await api(`/datasets/${activeDatasetId}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:name||undefined,domain})});}catch{}
  }
  setStep(1);await loadPeriods();nav("dashboard");
  // Show a success banner on the dashboard with a one-click Generate action.
  const li=lastImport||{};
  const el=$("import-banner");
  if(el){el.style.display="";el.className="banner ok";
    el.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
      <span>Imported · ${plural(li.rows||0,"row")} · ${plural(kpis.length,"KPI")} · ${plural(li.periods||0,"period")}.</span>
      <button class="btn sm primary" style="margin-left:auto" onclick="$('import-banner').style.display='none';generateAll()">Generate narratives</button>
      <button class="ico" style="border:none" onclick="$('import-banner').style.display='none'" aria-label="Dismiss">✕</button>`;
  }
  }catch(e){toast("Import failed to finalize: "+e.message);}
}

// Entry point: boot() handles auth (creates the Supabase client, checks the
// session) and then calls startApp(), which loads periods and applies the hash.
console.log("Closebrief UI build: v5.0.0 (ledger identity: green ink, serif brief, mono figures, verified seal)");
boot();
