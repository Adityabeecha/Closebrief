"""UI-standard regressions (docs/ui-standard.md): one format per thing, status
words that don't contradict, linkable views, and QA_REVIEW_2026-10 P1 #8/#9/#16.
Runs the real app.js functions in Node via the shared harness."""

from test_frontend_followup import run_js

_FORMATS = ("const fmt=", "/* ---------- reusable modal")


def test_money_and_percent_use_a_real_minus_sign():
    run_js(*_FORMATS, "", """
      assert.equal(fmt(-370000,'USD'),'−$370K');
      assert.equal(fmt(1200000,'USD'),'$1.20M');
      assert.equal(pct(-0.24),'−0.2%');
      assert.equal(pct(4.06),'+4.1%');
      assert.equal(pct(0),'0.0%');
      assert.equal(pct(null),null);
    """)


def test_periods_and_dates_read_as_words_not_iso():
    run_js(*_FORMATS, "", """
      assert.equal(fmtPeriod('2026-06'),'Jun 2026');
      assert.equal(fmtPeriod('2026-06','long'),'June 2026');
      assert.equal(fmtPeriod('2025-07','axis'),'Jul 25');
      assert.equal(fmtPeriod('2026-Q2'),'Q2 2026');
      assert.equal(fmtPeriod('2026'),'2026');
      assert.equal(fmtPeriod(null),'—');
      assert.equal(fmtDate('2026-08-02T10:11:12Z'),'02 Aug 2026');
      assert.equal(fmtDate(''),'—');
    """)


def test_figure_check_and_confidence_are_separate_statements():
    # The card said "Verified" while the detail said "Low confidence · numbers
    # passed": two checks blurred into one contradictory line.
    run_js("const REVIEW_LABELS=", "function renderReviewStrip(", "", """
      assert.equal(narrMeta({faithfulness:'passed',confidence:'low'}),
                   'Figures checked · Explanation: low confidence');
      assert.equal(narrMeta({faithfulness:'failed'}),'Figures need review');
      assert.equal(narrMeta({faithfulness:'unchecked'}),'');
      assert.equal(REVIEW_LABELS.none,'Not approved yet');
    """)


_NAV_SETUP = """
  let granularity='month';
  const pushed=[];let hash='';
  const location={get hash(){return hash;}};
  const history={pushState:(_s,_t,h)=>{pushed.push(h);hash=h;},replaceState:(_s,_t,h)=>{hash=h;}};
  const window={addEventListener(){}};
  const document={title:'',querySelector:()=>null,querySelectorAll:()=>[],body:{classList:{toggle(){}}}};
  const els={};const $=id=>(els[id]??={style:{},classList:{toggle(){}},setAttribute(){},removeAttribute(){},textContent:''});
  let detailFact=null,currentFacts=[],currentUser={role:'admin'},demoSession=false,activeDatasetObj=null,activeDatasetId=1;
  const closeSidebar=()=>{},loadContext=()=>{},loadAnalytics=()=>{},loadReview=()=>{},
        loadDigest=()=>{},loadCosts=()=>{},loadNotifications=()=>{},loadAdmin=()=>{};
  const api=async()=>{throw new Error('offline')};
  let currentPeriod='2026-06';
  const MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];
  const fmtPeriodTitle=p=>'June 2026 close';
  const fmtPeriod=p=>'Jun 2026';
  const plural=(n,one,many)=>`${n} ${n===1?one:(many||one+'s')}`;
  const esc=s=>String(s);
  function setDocTitle(t){document.title=t;}
"""


def test_navigation_pushes_history_so_back_works():
    # P1 #16: views used replaceState, so browser Back left the app.
    run_js("const VIEWS=", "/* ---------- auth", _NAV_SETUP, """
      nav('analyze');
      assert.deepEqual(pushed,['#analyze']);
      assert.equal(document.title,'Analyze');
      nav('kpis');
      assert.deepEqual(pushed,['#analyze','#month=2026-06&tab=kpis']);
      assert.equal(document.title,'KPIs · June 2026 close');
      nav('overview');
      assert.equal(document.title,'June 2026 close');
      // Older view names still navigate, to their new home.
      nav('dashboard');
      assert.equal(pushed.at(-1),'#month=2026-06&tab=kpis');
      nav('costs');
      assert.equal(pushed.at(-1),'#settings=costs');
      // Routing from Back/Forward must not push a new entry.
      const n=pushed.length;
      nav('digest',{fromRoute:true});
      assert.equal(pushed.length,n);
      // The detail view gets its own hash from openDetail, not from nav.
      nav('detail');
      assert.equal(pushed.length,n);
    """)


def test_routes_parse_new_and_legacy_links():
    run_js("const VIEWS=", "/* ---------- auth", _NAV_SETUP, """
      assert.deepEqual(parseRoute('#month=2026-06&tab=digest'),{tab:'digest',period:'2026-06',metric:null});
      assert.deepEqual(parseRoute('#month=2026-06&tab=kpis&metric=COGS%20%E2%80%93%20Dairy'),{tab:'kpis',period:'2026-06',metric:'COGS – Dairy'});
      // Links already sent in Slack and email keep working.
      assert.deepEqual(parseRoute('#metric=Net%20Revenue&period=2025-03'),{tab:'kpis',period:'2025-03',metric:'Net Revenue'});
      assert.deepEqual(parseRoute('#dashboard'),{tab:'kpis'});
      assert.deepEqual(parseRoute('#review'),{tab:'overview'});
      assert.deepEqual(parseRoute('#import'),{tab:'data'});
      assert.deepEqual(parseRoute('#analytics'),{place:'analyze'});
      assert.deepEqual(parseRoute('#settings=users'),{place:'admin'});
      assert.deepEqual(parseRoute('#data'),{place:'datahub'});
      assert.equal(parseRoute(''),null);
      assert.equal(parseRoute('#nonsense'),null);
      assert.deepEqual(parseRoute('#month=2026-06&tab=bogus'),{tab:'overview',period:'2026-06',metric:null});
      for(const r of [{tab:'boardpack',period:'2026-Q2'},{place:'library'},{tab:'kpis',period:'2026-06',metric:'A & B'}])
        assert.deepEqual(parseRoute(routeHash(r)),r.place?r:{metric:null,...r});
      assert.equal(landingTab('executive'),'digest');
      assert.equal(landingTab('analyst'),'overview');
    """)


def test_close_progress_and_next_step():
    run_js("const VIEWS=", "/* ---------- auth", _NAV_SETUP, """
      const f=(o)=>({has_data:true,narrative:'x',review_status:null,faithfulness:'passed',is_anomaly:false,...o});
      let s=closeProgress([],false);
      assert.equal(s.status,'empty');
      assert.deepEqual(closeNextStep(s),{label:'Import data',run:"nav('data')"});
      s=closeProgress([f({narrative:null}),f({}),f({has_data:false})],false);
      assert.equal(s.n,2);assert.equal(s.status,'in_progress');
      assert.equal(closeNextStep(s).label,'Draft 1 commentary');
      s=closeProgress([f({faithfulness:'failed'}),f({})],false);
      assert.equal(closeNextStep(s).label,'Check 1 flagged KPI');
      assert.equal(s.exceptions,1);
      s=closeProgress([f({review_status:'approved'}),f({})],false);
      assert.equal(closeNextStep(s).label,'Approve 1 draft');
      s=closeProgress([f({review_status:'approved'})],false);
      assert.equal(s.status,'ready');assert.equal(closeNextStep(s).label,'Send the digest');
      s=closeProgress([f({review_status:'approved'})],true);
      assert.equal(s.status,'shared');assert.equal(s.progress,1);assert.equal(closeNextStep(s),null);
      const steps=overviewSteps(closeProgress([f({narrative:null})],false),null);
      assert.deepEqual(steps.map(x=>x.id),['import','review','reasons','approve','share']);
      assert.deepEqual(steps.map(x=>x.state),['done','todo','done','blocked','blocked']);
      assert.equal(steps[2].target,'#month=2026-06&tab=context');
      // The server's checklist wins when it has one.
      assert.deepEqual(overviewSteps(s,{steps:[{id:'x'}]}),[{id:'x'}]);
    """)


def test_metric_hash_round_trips_names_with_spaces_and_symbols():
    run_js("const VIEWS=", "/* ---------- auth", _NAV_SETUP + "function metricHash(metric,period){return routeHash({tab:'kpis',period,metric});}", """
      const h=metricHash('COGS – Dairy & Packaging','2026-06');
      const p=new URLSearchParams(h.slice(1));
      assert.equal(p.get('metric'),'COGS – Dairy & Packaging');
      assert.equal(p.get('month'),'2026-06');
      assert.equal(p.get('tab'),'kpis');
    """)


def test_bulk_generate_refreshes_header_and_banner():
    # P1 #8: header said 0/26 while the tile said 26/26 and the banner still
    # offered to generate.
    run_js("async function generateAll(", "async function feedback(", """
      const calls=[];
      let currentUser={role:'admin'},granularity='month',currentPeriod='2026-06',activeDatasetId=1;
      let currentFacts=[{metric:'Rev',has_data:true,narrative:null}];
      let generatingSet=new Set(),skipRise=false;
      const els={'gen-all':{style:{}},'import-banner':{style:{},classList:{contains:()=>true}}};
      const $=id=>els[id];
      const ungeneratedFacts=()=>currentFacts.filter(f=>!f.narrative);
      const runPool=async(items,n,w)=>{for(const it of items)await w(it);};
      const api=async()=>({metric:'Rev',narrative:'Done.'});
      const applyGenerated=r=>{currentFacts[0].narrative=r.narrative;};
      const plural=(n,w)=>n+' '+w,toast=()=>{};
      const renderCards=()=>calls.push('cards'),updateGenButton=()=>calls.push('btn');
      const setFreshness=()=>calls.push('fresh'),maybePromptGenerate=()=>calls.push('banner');
    """, """
      await generateAll();
      assert.ok(calls.includes('fresh'));
      assert.ok(calls.includes('banner'));
      assert.equal(els['import-banner'].style.display,'none');
    """)


def test_generate_banner_hides_at_quarter_and_year_views():
    # P1 #8 (second half): the banner persisted into Q/Y views.
    run_js("function maybePromptGenerate(", "function dismissKey(", """
      let demoSession=false,granularity='quarter',currentPeriod='2026-Q2';
      const currentFacts=[{has_data:true,narrative:null}];
      const el={style:{display:''},classList:{contains:c=>c==='info'}};
      const $=()=>el;
    """, """
      maybePromptGenerate();
      assert.equal(el.style.display,'none');
    """)


def test_budget_join_reloads_the_dataset_it_attached_to():
    # P1 #9: "Done — view dashboard" showed stale "vs plan –" until reload.
    _join = """
      const calls=[];
      const resetImport=()=>calls.push('reset'),nav=v=>calls.push('nav:'+v);
      const activateDataset=async id=>calls.push('activate:'+id),loadPeriods=async()=>calls.push('periods');
    """
    run_js("async function finishBudgetJoin(", "async function applyTpl(", _join + "let activeDatasetId=4;", """
      await finishBudgetJoin(4);
      assert.deepEqual(calls,['reset','periods','nav:dashboard']);
    """)
    run_js("async function finishBudgetJoin(", "async function applyTpl(", _join + "let activeDatasetId=4;", """
      await finishBudgetJoin(7);
      assert.deepEqual(calls,['reset','activate:7','nav:dashboard']);
    """)


def test_cards_open_from_keyboard_but_not_from_inner_buttons():
    run_js("function cardKey(", "function clearCatFilter(", """
      const opened=[];const openDetail=i=>opened.push(i);
    """, """
      const card={};
      cardKey({key:'Enter',target:card,currentTarget:card,preventDefault(){}},2);
      cardKey({key:' ',target:card,currentTarget:card,preventDefault(){}},3);
      cardKey({key:'Enter',target:{},currentTarget:card,preventDefault(){}},4);
      cardKey({key:'a',target:card,currentTarget:card,preventDefault(){}},5);
      assert.deepEqual(opened,[2,3]);
    """)



def test_queue_groups_kpis_in_review_order():
    # The workspace queue: exceptions first, then drafts to review, then approved,
    # then KPIs with no data for the period.
    run_js("const QUEUE_GROUPS=", "function renderStatrow(", "", """
      assert.equal(queueGroup({has_data:false}),'nodata');
      assert.equal(queueGroup({has_data:true,is_anomaly:true,review_status:'approved'}),'exception');
      assert.equal(queueGroup({has_data:true,faithfulness:'failed'}),'exception');
      assert.equal(queueGroup({has_data:true,review_status:'approved'}),'approved');
      assert.equal(queueGroup({has_data:true,narrative:null}),'review');
      assert.deepEqual(QUEUE_GROUPS.map(g=>g[0]),['exception','review','approved','nodata']);
    """)


def test_queue_row_states_the_status_in_words():
    run_js("function queueRow(", "function markQueueSelection(", """
      const queueGroup=f=>!f.has_data?'nodata':f.is_anomaly?'exception':f.review_status==='approved'?'approved':'review';
      const hl=t=>t,esc=String,fmt=v=>'$'+v,pct=v=>(v>0?'+':'')+v+'%',fmtPeriod=p=>p,deltaText=()=>null;
      let detailFact=null,currentPeriod='2026-06',granularity='month';const generatingSet=new Set();
      const priorPeriodLabel=()=>'vs last month',anomalyWhy=()=>'';
    """, """
      const row=queueRow({metric:'Bad debt',has_data:true,is_anomaly:true,value:212,direction_good:'down',deltas:{budget_var_pct:136}},0,'');
      assert.match(row,/class="qi exception"/);assert.match(row,/Anomaly/);assert.match(row,/c-exc/);assert.match(row,/vs plan/);
      const ok=queueRow({metric:'Revenue',has_data:true,value:10,narrative:'x',review_status:'approved',deltas:{budget_var_pct:3.1}},1,'');
      assert.match(ok,/Approved/);assert.match(ok,/c-ok/);
      detailFact={metric:'Revenue'};
      assert.match(queueRow({metric:'Revenue',has_data:true,value:10,narrative:'x',deltas:{mom_pct:-2}},1,''),/aria-current="true"/);
    """)



def test_detail_read_only_approval_explains_permissions_and_demo_signup():
    run_js("function renderReviewStrip(", "async function reviewReport(", """
      const els={};const $=id=>(els[id]??={style:{},innerHTML:'',textContent:''});
      let role='viewer',demoSession=true;const canReview=()=>['admin','analyst'].includes(role);
      const REVIEW_LABELS={none:'Not approved yet'};
    """, r"""
      renderReviewStrip({report_id:1});
      assert.match($('d-readonly-approval').innerHTML,/<button[^>]+disabled>Approve/);
      assert.match($('d-readonly-approval').innerHTML,/Sign up to approve commentary/);
      assert.match($('d-readonly-approval').innerHTML,/exitDemo\(true\)/);
      demoSession=false;renderReviewStrip({report_id:1});
      assert.match($('d-readonly-approval').innerHTML,/Only analysts and admins can approve/);
      role='admin';renderReviewStrip({report_id:1});assert.equal($('d-readonly-approval').style.display,'none');
    """)



def test_dataset_display_name_only_normalizes_filenames():
    run_js(*_FORMATS, "", """
      assert.equal(datasetDisplayName('store_channel_pl_monthly.csv'),'Store channel pl monthly');
      assert.equal(datasetDisplayName('FY-2026_ACTUALS.XLSX'),'Fy 2026 actuals');
      assert.equal(datasetDisplayName('FP&A Actuals'),'FP&A Actuals');
      assert.equal(fmt(5,'%'),'5.0%');assert.equal(fmt(-5.64,'%'),'−5.6%');
    """)



def test_overview_no_budget_guidance_links_only_for_writers():
    run_js("const VIEWS=", "/* ---------- auth", _NAV_SETUP+"const fmt=v=>String(v),pct=v=>String(v),deltaText=()=>null,"
           "bigMove=f=>!!f.is_anomaly||Math.abs(f.deltas?.budget_var_pct||0)>10;", """
      currentFacts=[{metric:'Revenue',has_data:true,value:102000,unit:'USD',deltas:{mom_pct:-5.6},sources:[]}];
      paintOverview();let html=$('ov-glance').innerHTML;
      assert.match(html,/No budget loaded for this close: figures compare with last month/);
      assert.match(html,/href="#month=2026-06&tab=data"/);
      currentUser.role='viewer';paintOverview();html=$('ov-glance').innerHTML;
      assert.match(html,/Add a budget on the Data tab/);assert.doesNotMatch(html,/href=/);
      currentFacts[0].deltas.budget_var_pct=2;paintOverview();
      assert.doesNotMatch($('ov-glance').innerHTML,/No budget loaded/);
    """,)


def test_bulk_approve_skips_anomalies_flagged_and_approved_drafts():
    # Bulk approve is for drafts nobody needs to read one by one.
    run_js("function bulkApprovable(", "function renderBulkApprove(", "", """
      const f=o=>({has_data:true,report_id:1,narrative:'x',faithfulness:'passed',is_anomaly:false,review_status:null,...o});
      const ok=bulkApprovable([f({metric:'A'}),f({metric:'B',is_anomaly:true}),f({metric:'C',faithfulness:'failed'}),
        f({metric:'D',review_status:'approved'}),f({metric:'E',narrative:null}),f({metric:'F',has_data:false})]);
      assert.deepEqual(ok.map(x=>x.metric),['A']);
    """)


def test_reason_prompt_only_for_big_moves_without_a_cited_note():
    run_js("function reasonThreshold(", "function renderWhyNone(", "", """
      const f=o=>({has_data:true,is_anomaly:false,sources:[],deltas:{budget_var_pct:2},...o});
      assert.equal(needsReason(f({})),false);
      assert.equal(needsReason(f({is_anomaly:true})),true);
      assert.equal(needsReason(f({deltas:{budget_var_pct:-12}})),true);
      assert.equal(needsReason(f({deltas:{budget_var_pct:10}})),false);
      assert.equal(needsReason(f({is_anomaly:true,sources:[{id:'n1'}]})),false);
      assert.equal(needsReason(f({has_data:false,is_anomaly:true})),false);
    """)


def test_no_inline_handler_is_built_from_text():
    # S1: esc() turns ' into &#39;, which the browser decodes back to ' before
    # running an inline handler, so a KPI named "Men's" broke the button and a
    # crafted name could run script. Text must travel in data-arg instead.
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "ui" / "web"
    for name in ("app.js", "index.html"):
        src = (web / name).read_text(encoding="utf-8")
        bad = re.findall(r"""on[a-z]+="[^"]*'\$\{""", src)
        assert not bad, f"{name}: inline handler with interpolated text: {bad[:3]}"


def test_delegated_actions_pass_text_as_data():
    run_js("const DATA_ACTIONS=", "/* ---------- end delegated actions", """
      const calls=[];const store={};
      const selectClose=a=>calls.push(['close',a]),openMetricByName=a=>calls.push(['metric',a]),
            addReasonFor=a=>calls.push(['reason',a]),showSourceDoc=a=>calls.push(['source',a]);
      const els={banner:{style:{}}};const $=id=>els[id];
      const localStorage={setItem:(k,v)=>store[k]=v};
      let handler;const document={addEventListener:(t,h)=>handler=h};
    """, """
      const click=(act,arg,extra={})=>handler({preventDefault(){},target:{closest:()=>({dataset:{act,arg,...extra}})}});
      const odd=`Men's <b>Q1</b>`;
      click('metric',odd);click('reason',`O'Brien`);click('close','2026-06');click('source','ctx_001');
      click('dismiss',`cb:x:conflict:Note's`,{target:'banner'});
      assert.deepEqual(calls,[['metric',odd],['reason',`O'Brien`],['close','2026-06'],['source','ctx_001']]);
      assert.equal(store[`cb:x:conflict:Note's`],'1');assert.equal(els.banner.style.display,'none');
    """)


def test_bulk_drafting_stops_at_the_allowance_and_says_so():
    # S3: a 429 from the daily cap used to be swallowed and the run ended with
    # "All commentary drafted". It must stop trying and show the real reason.
    run_js("async function generateAll(", "async function feedback(", """
      const toasts=[];let tries=0;
      let currentUser={role:'analyst'},granularity='month',currentPeriod='2026-06',activeDatasetId=1;
      let currentFacts=['A','B','C','D'].map(m=>({metric:m,has_data:true,narrative:null}));
      let generatingSet=new Set(),skipRise=false;
      const els={'gen-all':{style:{}},'import-banner':{style:{},classList:{contains:()=>false}}};
      const $=id=>els[id];
      const ungeneratedFacts=()=>currentFacts.filter(f=>!f.narrative);
      const runPool=async(items,n,w)=>{for(const it of items)await w(it);};
      const api=async()=>{tries++;const e=new Error("You've used today's AI allowance ($0.50)");e.status=429;throw e;};
      const applyGenerated=()=>{};const plural=(n,w)=>n+' '+w,toast=m=>toasts.push(m);
      const renderCards=()=>{},updateGenButton=()=>{},setFreshness=()=>{},maybePromptGenerate=()=>{};
    """, """
      await generateAll();
      assert.equal(tries,1);
      assert.deepEqual(toasts,["You've used today's AI allowance ($0.50)"]);
    """)
