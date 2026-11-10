"""Exercise secondary screen states using real app.js loaders in Node."""
import pytest
from test_frontend_followup import SOURCE, run_js


def section(start, end):
    source = SOURCE.read_text(encoding="utf-8")
    return source[source.index(start):source.index(end, source.index(start))]


SETUP = r"""
  const XICON='<svg></svg>';   // shared close icon, defined once at module level in app.js
const els={},timers=new Map(),calls=[],visits=[];let clock=0,response=[],failure=null;
const $=id=>(els[id]??={innerHTML:'',textContent:'',value:'',disabled:false,hidden:false,style:{},attrs:{},wait:{hidden:true},
  setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]},
  querySelector(){return this.wait},focus(){this.focused=true},insertAdjacentHTML(_p,h){this.innerHTML+=h}});
const setTimeout=(f,ms)=>{const id=++clock;timers.set(id,{f,ms});return id},clearTimeout=id=>timers.delete(id);
const api=async(p,o)=>{calls.push([p,o]);if(failure)throw Error(failure);return typeof response==='function'?response(p,o):response};
const nav=v=>visits.push(v),toast=()=>{},errMsg=e=>e.message;
const ICON_PATHS={},updateReviewBadge=n=>{$('review-badge').textContent=n},onLever=()=>{};
const ensureCanvas=()=>{},chartReg={},chartTheme=()=>({}),mkChart=()=>{},tooltipCfg=()=>({});
let currentPeriod='2026-06',granularity='month',currentFacts=[],currentUser={role:'admin'},costsData=null;
let _anMetrics=[],_reviewItems=[],modalAnswer=false,modalCalls=[];
const canReview=()=>!['viewer','executive'].includes(currentUser.role);
const document={querySelectorAll:()=>[]},window={dispatchEvent(){}};
const EST_COST_PER_NARR=0.006,modalConfirm=async(...args)=>{modalCalls.push(args);return modalAnswer};
"""

LOADERS = [
    ("loadDigest", "digest-list", "async function loadDigest(", "/* ---------- context", "Nothing approved yet", "Open KPIs"),
    ("loadContext", "ctx-rows", "async function loadContext(", "async function addContext(", "No context", "Add a context note"),
    ("loadAnalytics", "analytics-state", "function _metricList(", "/* ---- Forecast", "No metrics", "Import data"),
    ("loadReview", "rq-list", "async function loadReview(", "async function openReviewItem(", "Nothing assigned to you", "Open KPIs"),
    ("loadCosts", "costs-state", "async function loadCosts(", "function deltaChip(", "No usage", "Open the dashboard"),
    ("loadNotifications", "nt-list", "async function loadNotifications(", "async function schCreate(", "No notification channels", "Add a channel"),
    ("loadSchedules", "sch-list", "function schKindFields(", "async function schCreate(", "No schedules", "Add a schedule"),
    ("loadAdmin", "admin-rows", "async function loadAdmin(", "async function setUserRole(", "No users", "Share the sign-in link"),
]


def run_screen(case, assertions, extra=""):
    _, _, start, end, *_ = case
    helpers = section("/* ---------- secondary screen states", "/* ================= ADVANCED ANALYTICS")
    if case[0] == "loadAnalytics":
        extra += "const loadCrossDomain=async()=>{};"
    if case[0] == "loadDigest":
        extra += section("const TICK=", "const DOCICON=")
        extra += section("let digestGenerating=false;", "async function loadDigest(")
        extra += "const closeShared=new Set(),loadCloseMeta=()=>{},setGranularity=()=>{};"
    formats = section("const fmt=", "/* ---------- reusable modal")
    run_js(start, end, SETUP + formats + helpers + extra, assertions)


@pytest.mark.parametrize("case", LOADERS, ids=lambda c:c[0])
def test_empty_screen_explains_purpose_and_offers_next_action(case):
    loader, box, _, _, title, action = case
    run_screen(case, f"""
      await {loader}();const html=$('{box}').innerHTML;
      assert.match(html,/<h2|<h3/);assert.ok(html.includes('{title}'),html);
      assert.ok(html.includes('{action}'),html);assert.match(html,/<p>/);assert.match(html,/<button/);
      assert.equal(timers.size,0);
    """)


@pytest.mark.parametrize("case", LOADERS, ids=lambda c:c[0])
@pytest.mark.parametrize("reason", ["403", "Failed to fetch", "HTTP 500 <internal>"])
def test_error_screen_explains_failure_and_retry_reloads(case, reason):
    loader, box, *_ = case
    run_screen(case, f"""
      failure={reason!r};await {loader}();const html=$('{box}').innerHTML;
      assert.match(html,/Retry/);assert.ok(html.includes('{loader}()'),html);
      assert.doesNotMatch(html,/403|500|<internal>/);
      assert.match(html,/{'admin access' if reason == '403' else 'connection' if reason == 'Failed to fetch' else 'try again'}/i);
      const handler=html.match(/onclick="([^"]+)"/)[1];failure=null;
      await eval(handler.replaceAll('&#39;',"'").replaceAll('&quot;','"'));
      assert.doesNotMatch($('{box}').innerHTML,/Retry/);assert.equal(timers.size,0);
    """)


@pytest.mark.parametrize("case", LOADERS, ids=lambda c:c[0])
def test_loading_names_screen_and_reassures_after_four_seconds(case):
    loader, box, *_ = case
    run_screen(case, f"""
      let resolve;response=new Promise(r=>resolve=r);
      const pending={loader}();assert.match($('{box}').innerHTML,/Loading (the |saved |notification |cost |user |advanced |context |schedule)/i);
      assert.match($('{box}').innerHTML,/class="sk/);assert.equal($('{box}').wait.hidden,true);
      for(const timer of timers.values()){{assert.equal(timer.ms,4000);timer.f();}}
      assert.equal($('{box}').wait.hidden,false);assert.match($('{box}').innerHTML,/Still working/i);
      resolve([]);await pending;assert.equal(timers.size,0);
    """)











def test_analytics_sorts_both_dropdowns_by_latest_absolute_value_and_keeps_selection():
    run_screen(LOADERS[2], r"""
      currentFacts=[{metric:'Small',value:20,unit:'USD',has_data:true},{metric:'Loss',value:-900,unit:'USD',has_data:true},
        {metric:'Large',value:500,unit:'USD',has_data:true},{metric:'Missing',value:null,has_data:true},{metric:'No data',value:9999,has_data:false}];
      $('scn-metric').value='Small';await loadAnalytics();
      assert.deepEqual(_anMetrics.map(m=>m.name),['Loss','Large','Small','Missing']);
      for(const id of ['fc-metric','scn-metric'])assert.ok($(id).innerHTML.indexOf('Loss')<$(id).innerHTML.indexOf('Large'));
      assert.equal($('scn-metric').value,'Small');assert.equal(currentFacts[0].metric,'Small');
    """)


def test_review_context_users_and_schedules_format_dates_and_label_controls():
    run_screen(LOADERS[3], r"""
      response=[{metric:'Revenue',period:'2026-06',preview:'Ready',dataset_name:'Finance'}];await loadReview();
      assert.match($('rq-list').innerHTML,/Jun 2026/);assert.equal($('review-badge').textContent,1);
      response=[];await loadReview();assert.equal($('review-badge').textContent,0);
    """)
    run_screen(LOADERS[1], r"""
      response=[{id:1,type:'event_note',title:'Pricing',body:'Changed',metric_tags:[],effective_date:'2026-06'}];await loadContext();
      assert.match($('ctx-rows').innerHTML,/Jun 2026/);assert.match($('ctx-rows').innerHTML,/aria-label="Delete/);
    """)
    run_screen(LOADERS[7], r"""
      response=[{user_id:'abc',email:'admin@example.com',role:'admin',created_at:'2026-07-02'}];await loadAdmin();
      assert.match($('admin-rows').innerHTML,/02 Jul 2026/);assert.match($('admin-rows').innerHTML,/aria-label="Role for admin@example.com"/);
    """)
    run_screen(LOADERS[6], r"""
      response=[{id:1,kind:'digest',cadence:'daily',enabled:true,config:{},next_run_at:'2026-07-02T10:00:00Z'}];await loadSchedules();
      assert.match($('sch-list').innerHTML,/02 Jul 2026/);assert.doesNotMatch($('sch-list').innerHTML,/2026-07-02/);
    """)


def test_costs_format_money_and_days_with_standard_helpers():
    run_screen(LOADERS[4], r"""
      response={total_cost_usd:102000,total_llm_calls:2,cache:{},latency:{},by_day:[{day:'2026-07-02',endpoint:'/digest',calls:2,cost_usd:12.5}]};
      await loadCosts();assert.match($('costs-stats').innerHTML,/\$102K/);
      assert.match($('costs-table').innerHTML,/02 Jul 2026/);assert.match($('costs-table').innerHTML,/\$12\.5/);
    """)


def test_cross_dataset_signals_have_loading_empty_and_retry_states():
    case = ('loadCrossDomain', 'xd-list', 'async function loadCrossDomain(',
            '/* ---- Collaborative review', 'No cross-dataset signals', 'Import another dataset')
    run_screen(case, r"""
      response=[];await loadCrossDomain();assert.match($('xd-list').innerHTML,/No cross-dataset signals.*Import another dataset/s);
      failure='403';await loadCrossDomain();assert.match($('xd-list').innerHTML,/admin access.*loadCrossDomain\(\).*Retry/s);
      assert.equal(timers.size,0);
    """)





def test_schedule_failed_runs_explain_recovery_without_raw_provider_errors():
    run_screen(LOADERS[6], r"""
      response=[{id:1,kind:'digest',cadence:'daily',enabled:true,config:{},last_status:'error',fail_count:2,
        last_error:'HTTP 429 provider secret diagnostic'}];await loadSchedules();
      const html=$('sch-list').innerHTML;assert.doesNotMatch(html,/429|provider secret/);
      assert.match(html,/last run: error/);assert.match(html,/Check.*channel.*configuration/i);
    """)


def test_a_newer_review_request_wins_and_clears_old_loading_timer():
    run_screen(LOADERS[3], r"""
      let resolveOld;response=new Promise(r=>resolveOld=r);const old=loadReview();
      response=[{metric:'Fresh review',period:'2026-06'}];await loadReview();
      resolveOld([{metric:'Stale review',period:'2026-05'}]);await old;
      assert.match($('rq-list').innerHTML,/Fresh review/);assert.doesNotMatch($('rq-list').innerHTML,/Stale review/);
      assert.equal(timers.size,0);assert.equal($('rq-list').attrs['aria-busy'],undefined);
    """)


def test_read_only_context_empty_state_offers_a_reachable_action():
    run_screen(LOADERS[1], r"""
      currentUser.role='executive';await loadContext();assert.match($('ctx-rows').innerHTML,/Ask an analyst/);
      assert.match($('ctx-rows').innerHTML,/Open KPIs/);assert.doesNotMatch($('ctx-rows').innerHTML,/c-title/);
    """)





def test_analytics_empty_period_does_not_claim_the_dataset_has_no_metrics():
    run_screen(LOADERS[2], r"""
      currentFacts=[{metric:'Revenue',value:null,has_data:false,unit:'USD'}];await loadAnalytics();
      const html=$('analytics-state').innerHTML;assert.match(html,/No metrics for Jun 2026/);
      assert.match(html,/Choose a period/);assert.match(html,/Open the dashboard/);assert.doesNotMatch(html,/Import data/);
    """)


_DIGEST = {"period": "2026-06", "total_kpis": 3, "approved_kpis": 1, "pending": ["Cash", "Cost"], "ready": False,
           "sent_at": None, "items": [{"metric": "<Revenue>", "headline": "Revenue fell to $4.2M.",
                                       "detail": "Pricing churned legacy accounts.", "budget_var_abs": -370000,
                                       "budget_var_pct": -8.1, "faithfulness": "passed", "report_id": 7}]}


def _digest_response(digest, runs="[]"):
    import json
    return f"response=p=>p.startsWith('/digest?')?{json.dumps(digest)}:{runs};"


def test_digest_shows_only_approved_items_and_what_is_still_waiting():
    run_screen(LOADERS[0], _digest_response(_DIGEST) + r"""
      await loadDigest();const html=$('digest-list').innerHTML;
      assert.ok(calls.some(([p])=>p==='/digest?period=2026-06&top_n=5'));
      assert.ok(!calls.some(([p,o])=>o&&o.method==='POST'),'loading never generates or sends');
      assert.match(html,/Revenue fell to \$4\.2M\./);assert.match(html,/&lt;Revenue&gt;/);
      assert.match(html,/2 KPIs still need approval/);assert.match(html,/Cash, Cost/);assert.match(html,/Open KPIs/);
      assert.match(html,/1 of 3 KPIs approved/);assert.match(html,/Not sent yet/);
      assert.equal($('digest-generate').style.display,'none');
    """)


def test_ready_digest_offers_send_and_a_sent_one_says_when():
    ready = {**_DIGEST, "approved_kpis": 3, "pending": [], "ready": True}
    run_screen(LOADERS[0], _digest_response(ready) + r"""
      await loadDigest();assert.match($('digest-list').innerHTML,/Every KPI is approved/);
      assert.equal($('digest-generate').style.display,'');
    """)
    sent = {**ready, "sent_at": "2026-07-03T09:00:00Z"}
    run_screen(LOADERS[0], _digest_response(sent) + r"""
      await loadDigest();assert.match($('digest-list').innerHTML,/Sent 03 Jul 2026/);
      assert.equal($('digest-generate').style.display,'none');
    """)


def test_sending_needs_confirmation_and_cancel_sends_nothing():
    ready = {**_DIGEST, "approved_kpis": 3, "pending": [], "ready": True}
    run_screen(LOADERS[0], _digest_response(ready) + r"""
      await loadDigest();calls.length=0;
      modalAnswer=false;await sendDigest();assert.equal(modalCalls.length,1);assert.equal(calls.length,0);
      modalAnswer=true;await sendDigest();
      assert.ok(calls.some(([p,o])=>p==='/digest/send?period=2026-06&top_n=5'&&o.method==='POST'));
      assert.equal($('digest-generate').disabled,false);
    """)


def test_read_only_people_cannot_send(  ):
    ready = {**_DIGEST, "approved_kpis": 3, "pending": [], "ready": True}
    run_screen(LOADERS[0], _digest_response(ready) + r"""
      currentUser.role='executive';await loadDigest();calls.length=0;modalAnswer=true;
      await sendDigest();assert.equal(calls.length,0);assert.equal(modalCalls.length,0);
    """)


def test_digest_marks_each_items_figure_check():
    for status, pattern, present in (("passed", "Figures checked", True), ("failed", "Needs review", True),
                                     ("unchecked", "Figures checked|Needs review", False)):
        d = {**_DIGEST, "items": [{**_DIGEST["items"][0], "faithfulness": status}]}
        check = "assert.match" if present else "assert.doesNotMatch"
        run_screen(LOADERS[0], _digest_response(d) + f"""
          await loadDigest();{check}($('digest-list').innerHTML,/{pattern}/);
        """)


def test_quarter_and_year_views_point_to_months():
    run_screen(LOADERS[0], r"""
      granularity='quarter';currentPeriod='2026-Q2';response=[];
      await loadDigest();assert.match($('digest-list').innerHTML,/Digests are prepared per month/);
      assert.ok(!calls.some(([p])=>p.startsWith('/digest?')));
    """)

