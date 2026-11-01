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
                   'Figures verified · Explanation: low confidence');
      assert.equal(narrMeta({faithfulness:'failed'}),'Figures need review');
      assert.equal(narrMeta({faithfulness:'unchecked'}),'');
      assert.equal(REVIEW_LABELS.none,'Not approved yet');
    """)


_NAV_SETUP = """
  const pushed=[];let hash='';
  const location={get hash(){return hash;}};
  const history={pushState:(_s,_t,h)=>{pushed.push(h);hash=h;}};
  const window={addEventListener(){}};
  const document={title:'',querySelector:()=>null};
  const els={};const $=id=>(els[id]??={style:{}});
  const closeSidebar=()=>{},loadContext=()=>{},loadAnalytics=()=>{},loadReview=()=>{},
        loadDigest=()=>{},loadCosts=()=>{},loadNotifications=()=>{},loadAdmin=()=>{};
  let currentPeriod='2026-06';
  const fmtPeriodTitle=p=>'June 2026 close';
  function setDocTitle(t){document.title=t;}
"""


def test_navigation_pushes_history_so_back_works():
    # P1 #16: views used replaceState, so browser Back left the app.
    run_js("const VIEWS=", "/* ---------- auth", _NAV_SETUP, """
      nav('review');
      assert.deepEqual(pushed,['#review']);
      assert.equal(document.title,'Review queue');
      nav('dashboard');
      assert.deepEqual(pushed,['#review','#dashboard']);
      assert.equal(document.title,'June 2026 close');
      // Routing from Back/Forward must not push a new entry.
      nav('digest',{fromRoute:true});
      assert.equal(pushed.length,2);
      // The detail view gets its own hash from openDetail, not from nav.
      nav('detail');
      assert.equal(pushed.length,2);
    """)


def test_metric_hash_round_trips_names_with_spaces_and_symbols():
    run_js("function metricHash(", "function openDetail(", "", """
      const h=metricHash('COGS – Dairy & Packaging','2026-06');
      const p=new URLSearchParams(h.slice(1));
      assert.equal(p.get('metric'),'COGS – Dairy & Packaging');
      assert.equal(p.get('period'),'2026-06');
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
