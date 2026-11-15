"""End-to-end tester feedback, exercised against the real UI functions in Node."""

from pathlib import Path

from test_frontend_followup import run_js

DOM = """
  const els={};const $=id=>(els[id]??={value:'',style:{},textContent:'',innerHTML:'',disabled:false});
  const esc=s=>String(s).replaceAll('<','&lt;'),errMsg=e=>e.message;
  const toasts=[];const toast=t=>toasts.push(t);
  let finish;let fail=false;let api=()=>new Promise((resolve,reject)=>{
    finish=r=>fail?reject(Error('Offline')):resolve(r);
  });
"""


def test_create_kpi_resets_all_fields_and_recovers_button():
    run_js("async function createDerivedKpi(", "/* ---- Collaborative review", DOM + """
      const loadFacts=async()=>{},_metricList=()=>[],_fillMetricSelect=()=>{};let _anMetrics=[];
    """, """
      $('kb-name').value='Margin';$('kb-formula').value='[Profit]/[Revenue]';
      $('kb-unit').value='USD';$('kb-cat').value='Custom';$('kb-dir').value='down';
      const p=createDerivedKpi();assert.equal($('kb-btn').disabled,true);finish({});await p;
      assert.match(toasts[0],/Created/);assert.equal($('kb-btn').disabled,false);
      for(const id of ['kb-name','kb-formula'])assert.equal($(id).value,'');
      assert.equal($('kb-unit').value,'%');assert.equal($('kb-cat').value,'Derived');assert.equal($('kb-dir').value,'up');
      $('kb-name').value='Again';$('kb-formula').value='[Revenue]';fail=true;
      const q=createDerivedKpi();finish();await q;assert.equal($('kb-btn').disabled,false);
      assert.equal($('kb-name').value,'Again');assert.match(toasts.at(-1),/Offline/);
    """)


def test_forecast_renders_answer_and_inline_error_and_clears_thinking():
    run_js("async function forecastNarrative(", "/* ---- Scenario", DOM, """
      $('fc-metric').value='Revenue';$('fc-narr-btn').textContent='Explain this forecast';
      let p=forecastNarrative();assert.match($('fc-narr-btn').textContent,/Thinking/);
      finish({narrative:'<Answer>'});await p;
      assert.match($('fc-narr').innerHTML,/&lt;Answer>/);assert.equal($('fc-narr-btn').disabled,false);
      assert.equal($('fc-narr-btn').textContent,'Explain this forecast');
      fail=true;p=forecastNarrative();finish();await p;
      assert.match($('fc-narr').innerHTML,/Offline/);assert.doesNotMatch($('fc-narr').innerHTML,/Answer/);
      assert.equal($('fc-narr-btn').disabled,false);
    """)


def test_scenario_shows_updating_until_result_or_error():
    run_js("function runScenario(", "/* ---- Custom KPI", DOM + """
      let _scnT;let timer;const clearTimeout=()=>{},setTimeout=fn=>{timer=fn;return 1};
      const _anMetrics=[];const fmt=String;
    """, """
      $('scn-metric').value='Revenue';runScenario();let p=timer();
      assert.match($('scn-out').innerHTML,/Updating/);
      assert.match($('scn-out').innerHTML,/padding:6px 0/);
      finish({impact_abs:1,base_value:10,projected_value:11});await p;
      assert.match($('scn-out').innerHTML,/Projected/);assert.doesNotMatch($('scn-out').innerHTML,/Updating/);
      fail=true;runScenario();p=timer();finish();await p;
      assert.match($('scn-out').innerHTML,/Offline/);assert.doesNotMatch($('scn-out').innerHTML,/Updating/);
    """)


def test_approval_link_disables_button_through_request_and_recovers():
    run_js("async function approvalLink(", "// Advanced tools", DOM + """
      let currentPeriod='2026-06',granularity='month';const fmtPeriodTitle=String;
      const navigator={clipboard:{writeText:async()=>{}}};const _openModalRaw=async()=>{};
    """, """
      $('digest-approval').textContent='Get approval link';
      let p=approvalLink();assert.equal($('digest-approval').disabled,true);
      assert.match($('digest-approval').textContent,/Working/);finish({url:'https://example.com',days:7});await p;
      assert.equal($('digest-approval').disabled,false);assert.equal($('digest-approval').textContent,'Get approval link');
      fail=true;p=approvalLink();finish();await p;assert.equal($('digest-approval').disabled,false);
      assert.match(toasts.at(-1),/Offline/);
    """)


def test_send_digest_is_busy_even_during_recipient_lookup_and_recovers():
    run_js("async function sendDigest(", "/* ---------- context", DOM + """
      let digestGenerating=false,digestNow={ready:true,items:[{}]},currentPeriod='2026-06';
      const canReview=()=>true,fmtPeriod=String,plural=(n,w)=>n+' '+w,recipientsText=()=>'';
      let confirm=true;const modalConfirm=async()=>confirm;
      const closeShared=new Set(),loadDigestHistory=async()=>{},loadCloseMeta=()=>{};
    """, """
      $('digest-generate').textContent='Send to leadership';
      let p=sendDigest();assert.equal($('digest-generate').disabled,true);
      assert.match($('digest-generate').textContent,/Working/);
      finish([]);await new Promise(setImmediate);finish({});await p;
      assert.equal($('digest-generate').disabled,false);assert.equal(digestGenerating,false);
      confirm=false;p=sendDigest();finish([]);await p;assert.equal($('digest-generate').disabled,false);
      confirm=true;p=sendDigest();finish([]);await new Promise(setImmediate);fail=true;finish();await p;
      assert.equal($('digest-generate').disabled,false);assert.match(toasts.at(-1),/Offline/);
    """)


def test_approved_failed_check_no_longer_counts_as_flagged():
    run_js("function closeProgress(", "const CLOSE_STATUS=", "", """
      const s=closeProgress([{has_data:true,narrative:'Draft',faithfulness:'failed',review_status:'approved'}],false);
      assert.equal(s.failed,0);assert.equal(s.exceptions,0);assert.equal(s.approved,1);
    """)


def test_review_refreshes_queue_header_and_sidebar_immediately():
    run_js("async function reviewReport(", "async function toggleVersions(", DOM + """
      let detailFact={report_id:4,faithfulness:'passed'},currentFacts=[{report_id:4}],currentPeriod='2026-06';
      let boardPackFor=1,overviewFor=1;
      const closeMonths={'2026-06':{kpis:1,approved:0,drafted:1,progress:0.5,status:'in_progress'}};
      const calls=[];const renderReviewStrip=()=>{},renderMonthHead=()=>calls.push('header'),
        renderCards=()=>calls.push('queue'),renderCloses=()=>calls.push('sidebar'),loadCloseMeta=()=>calls.push('server');
      const closeShared=new Set();const closeProgress=()=>({approved:1,drafted:1,progress:.9,status:'ready'});
    """, """
      const p=reviewReport('approved');finish({});await p;
      assert.equal(currentFacts[0].review_status,'approved');assert.equal(detailFact.review_status,'approved');
      assert.ok(calls.includes('queue'));assert.ok(calls.includes('header'));assert.ok(calls.includes('sidebar'));
      assert.equal(closeMonths[currentPeriod].approved,1);assert.equal(closeMonths[currentPeriod].progress,.9);
    """)


def test_approve_hidden_after_approval_but_other_actions_remain():
    run_js("function renderReviewStrip(", "async function reviewReport(", DOM + """
      const canReview=()=>true,demoSession=false,REVIEW_LABELS={approved:'Approved'};
    """, """
      renderReviewStrip({report_id:1,review_status:'approved'});assert.equal($('d-approve').style.display,'none');
      assert.notEqual($('d-rev-strip').style.display,'none');
      renderReviewStrip({report_id:1});assert.equal($('d-approve').style.display,'');
    """)


def test_reviewer_metadata_omits_cost():
    run_js("function narrMeta(", "function renderReviewStrip(", "", """
      assert.equal(narrMeta({faithfulness:'passed',confidence:'high',cost_usd:.0032}),
        'Numbers match the data · Reason: strong');
    """)


def test_context_library_keeps_scopes_and_searches_title_body_and_tags():
    run_js("function ctxInClose(", "function paintContextDetail(", DOM + """
      let currentPeriod='2026-06',granularity='month';const fmtPeriod=String,fmtDate=String,XICON='';
      const paintContextDetail=()=>{};
    """, """
      ctxScope='library';ctxDocs=[
        {id:1,title:'Pricing',body:'New rate',metric_tags:['sales'],type:'event_note',effective_date:'2026-06'},
        {id:2,title:'Hiring',body:'Engineering expanded',metric_tags:['people'],type:'event_note',effective_date:'2026-05'}];
      renderContextList();assert.match($('ctx-scope').innerHTML,/This close · 1/);
      assert.match($('ctx-scope').innerHTML,/All notes · 2/);
      for(const q of ['HIRING','engineering','people']){
        $('ctx-search').value=q;renderContextList();assert.match($('ctx-rows').innerHTML,/Hiring/);
        assert.doesNotMatch($('ctx-rows').innerHTML,/Pricing/);
      }
      $('ctx-search').value='absent';renderContextList();assert.match($('ctx-rows').innerHTML,/No notes match/);
      $('ctx-search').value='';ctxScope='close';renderContextList();assert.match($('ctx-rows').innerHTML,/Pricing/);
      assert.doesNotMatch($('ctx-rows').innerHTML,/Hiring/);
    """)


def test_notification_empty_states_omit_actions_while_forms_are_open():
    setup = DOM + """
      const screenLoading=()=>()=>true,screenError=String,ICON_PATHS={};
      const screenState=(title,body,action,label)=>title+(action?'<button>'+label+'</button>':'');
    """
    run_js("async function loadNotifications(", "/* ---------- schedules", setup + "const loadSchedules=async()=>{};", """
      let p=loadNotifications();finish([]);await p;assert.doesNotMatch($('nt-list').innerHTML,/<button>/);
      $('nt-form').hidden=true;p=loadNotifications();finish([]);await p;assert.match($('nt-list').innerHTML,/Add a channel/);
    """)
    run_js("async function loadSchedules(", "async function schCreate(", setup, """
      let p=loadSchedules();finish([]);await p;assert.doesNotMatch($('sch-list').innerHTML,/<button>/);
      $('sch-form').hidden=true;p=loadSchedules();finish([]);await p;assert.match($('sch-list').innerHTML,/Add a schedule/);
    """)


def test_context_scopes_survive_loading_and_an_empty_library():
    run_js("async function loadContext(", "function paintContextDetail(", DOM + """
      let currentPeriod='2026-06',granularity='month',currentUser={role:'admin'};
      const screenLoading=()=>()=>true,screenError=String,screenState=()=>'';
      const fmtPeriod=String,fmtDate=String,XICON='',paintContextDetail=()=>{};
    """, """
      ctxScope='library';let p=loadContext();
      finish([{id:1,title:'Note',body:'Reason',metric_tags:[],type:'event_note'}]);await p;
      assert.match($('ctx-scope').innerHTML,/This close · 1/);assert.match($('ctx-scope').innerHTML,/All notes · 1/);
      p=loadContext();finish([]);await p;
      assert.match($('ctx-scope').innerHTML,/This close · 0/);assert.match($('ctx-scope').innerHTML,/All notes · 0/);
    """)


def test_history_restores_older_text_through_normal_edit_flow():
    run_js("async function toggleVersions(", "function paintScaleBanner(", DOM + """
      let detailFact={report_id:7},locked=false;const canReview=()=>true,closeLocked=()=>locked;
      const restored=[];const editNarr=async(id,text)=>restored.push([id,text]);
    """, """
      $('d-versions').style.display='none';let p=toggleVersions();
      finish([{version:1,narrative:'Old <text>'},{version:2,narrative:'Current'}]);await p;
      assert.equal(($('d-versions').innerHTML.match(/Restore this version/g)||[]).length,1);
      await $('d-versions').onclick({target:{closest:()=>({dataset:{version:'1'}})}});
      assert.deepEqual(restored,[[7,'Old <text>']]);
      locked=true;$('d-versions').style.display='none';p=toggleVersions();
      finish([{version:1,narrative:'Old'},{version:2,narrative:'Current'}]);await p;
      assert.doesNotMatch($('d-versions').innerHTML,/Restore this version/);
    """)


def test_restore_saves_as_edited_feedback_and_reloads_figure_check():
    run_js("async function editNarr(", "/* ---------- digest", DOM + """
      const currentFacts=[{report_id:7,narrative:'Current',metric:'Revenue'}],shownFacts=[];
      const modalInput=()=>{throw Error('Restore should use selected text')};
      let overviewFor=1,reloaded=false;const loadFacts=async()=>{reloaded=true},loadCloseMeta=()=>{},plural=()=>'';
      const openDetail=()=>{};
      const originalApi=api;let payload;
      api=(path,opts)=>{assert.equal(path,'/feedback');payload=JSON.parse(opts.body);return originalApi()};
    """, """
      const p=editNarr(7,'Old text');finish({unverified:['123']});await p;
      assert.deepEqual(payload,{report_id:7,action:'edited',edited_text:'Old text'});
      assert.equal(reloaded,true);assert.match(toasts[0],/flagged for review/);
    """)


def test_history_response_cannot_restore_into_a_different_kpi():
    run_js("async function toggleVersions(", "function paintScaleBanner(", DOM + """
      let detailFact={report_id:7};const canReview=()=>true,closeLocked=()=>false;
      const editNarr=()=>{throw Error('Must not restore stale history')};
    """, """
      $('d-versions').style.display='none';const p=toggleVersions();
      detailFact={report_id:8};$('d-versions').innerHTML='KPI B history';
      finish([{version:1,narrative:'A old'},{version:2,narrative:'A current'}]);await p;
      assert.equal($('d-versions').innerHTML,'KPI B history');
    """)


def test_print_label_search_control_and_sidebar_spacing():
    html = (Path(__file__).parents[1] / "ui/web/index.html").read_text(encoding="utf-8")
    assert 'onclick="exportPDF()">Print / save as PDF</button>' in html
    assert 'id="ctx-search"' in html
    assert 'id="d-approve"' in html
    assert '.adv-toggle{flex-shrink:0;' in html
    assert '.nav{min-height:0;' in html
