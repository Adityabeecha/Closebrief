"""Run actual standalone UI functions in Node with controlled browser boundaries."""

import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "ui/web/app.js"


def test_import_kpi_names_preserve_acronyms_and_remove_currency_suffix():
    run_js("function humanizeKpiName(", "function kpiRows(", "", """
      for(const [source,want] of [['closed_won_arr_usd','Closed-won ARR'],['mqls','MQLs'],
        ['cogs_coffee','COGS coffee'],['net_revenue_usd','Net revenue'],['customer_count','Customer count'],
        ['ARR','ARR'],['Net Revenue','Net Revenue'],['usd','USD']])assert.equal(humanizeKpiName(source),want);
    """)


KPI_SELECTION_SETUP = """
  const rows=[true,false,true].map((included,i)=>({dataset:{src:'source_'+i},fields:{
    '.k-include':{checked:included},'.k-select':{checked:i<2},'.k-cat':{value:'Old'},
    '.k-unit':{value:'USD'},'.k-dir':{value:'up'},'.k-name':{value:'Name '+i}},
    querySelector(s){return this.fields[s]}}));
  const els={'kpi-bulk-category':{value:'Costs'},'kpi-bulk-unit':{value:'count'},'kpi-bulk-direction':{value:'down'}};
  const $=id=>(els[id]??={style:{},textContent:'',disabled:false});
  const document={querySelectorAll:()=>rows};
  let kpiLoading=false,kpiSaving=false,importLeaving=false;const toast=()=>{},plural=(n,w)=>n+' '+w;
"""


def test_import_include_none_disables_finish_and_all_restores_it():
    run_js("function kpiRows(", "async function loadKpiPicker(", KPI_SELECTION_SETUP, """
      selectKpis(false);assert.ok(rows.every(r=>!r.fields['.k-include'].checked));
      assert.equal($('imp-finish').disabled,true);assert.match($('kpi-selection').textContent,/pick at least one/i);
      selectKpis(true);assert.ok(rows.every(r=>r.fields['.k-include'].checked));assert.equal($('imp-finish').disabled,false);
      assert.equal(rows[2].fields['.k-select'].checked,false);
    """)


def test_import_bulk_edit_changes_only_selected_rows_and_requested_fields():
    run_js("function kpiRows(", "async function loadKpiPicker(", KPI_SELECTION_SETUP, """
      applyKpiBulk();for(const r of rows.slice(0,2))assert.deepEqual(
        ['.k-cat','.k-unit','.k-dir'].map(s=>r.fields[s].value),['Costs','count','down']);
      assert.equal(rows[2].fields['.k-cat'].value,'Old');assert.equal(rows[1].fields['.k-include'].checked,false);
      $('kpi-bulk-category').value='';$('kpi-bulk-unit').value='';$('kpi-bulk-direction').value='up';
      applyKpiBulk();assert.equal(rows[0].fields['.k-cat'].value,'Costs');assert.equal(rows[0].fields['.k-unit'].value,'count');
      selectKpiEdits(false);assert.equal($('kpi-bulk-apply').disabled,true);
    """)


def test_import_leave_cancels_or_deletes_only_the_unfinished_dataset():
    run_js("function hasUnfinishedImport(", "function dropFile(", """
      let importStep=3,lastImport={datasetId:7},kpiSaving=false,importLeaving=false;
      const calls=[],els={};const $=id=>(els[id]??={style:{display:''},innerHTML:''});
      let answer=null,fail=false;const modalConfirm=async title=>{assert.equal(title,'Leave import?');return answer};
      const api=async(p,o)=>{calls.push([p,o.method]);if(fail)throw Error('Cannot delete')};
      const updateKpiSelection=()=>{},closeSidebar=()=>{},resetImport=()=>{lastImport=null;importStep=1};
      const loadPeriods=async()=>{},toast=()=>{},esc=String;
    """, """
      assert.equal(await leaveImport(),false);assert.deepEqual(calls,[]);assert.equal(lastImport.datasetId,7);
      answer=true;fail=true;assert.equal(await leaveImport(),false);assert.equal(lastImport.datasetId,7);
      assert.match($('imp-result').innerHTML,/Cannot delete/);
      fail=false;assert.equal(await leaveImport(),true);assert.equal(lastImport,null);
      assert.deepEqual(calls,[['/datasets/7','DELETE'],['/datasets/7','DELETE']]);
      assert.equal(await leaveImport(),true);assert.equal(calls.length,2);
    """)


def test_import_steps_expose_current_and_completed_status():
    run_js("function setStep(", "function hasUnfinishedImport(", """
      let importStep=1;const els={};const esc=String;
      const $=id=>(els[id]??={style:{},attrs:{},setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]}});
    """, """
      setStep(3);assert.equal($('st-3').attrs['aria-current'],'step');
      assert.equal($('st-1').attrs['aria-current'],undefined);assert.match($('st-1').innerHTML,/step-tick/);
      assert.match($('st-2').innerHTML,/done/);assert.equal($('imp-kpis').style.display,'');
      setStep(1);assert.equal($('st-3').attrs['aria-current'],undefined);assert.doesNotMatch($('st-1').innerHTML,/done/);
    """)


def test_import_history_navigation_keeps_import_open_until_cleanup_succeeds():
    run_js("async function importRouteChanged(", "function dropFile(", """
      const routerReady=true,location={hash:'#month=2026-06&tab=kpis'},visits=[];let allow=false;
      const currentPlace={tab:'data'},currentPeriod='2026-06',routeHash=r=>`#month=${r.period}&tab=${r.tab}`;
      const hasUnfinishedImport=()=>true,leaveImport=async()=>allow,route=()=>visits.push(location.hash);
      const history={pushState(a,b,h){location.hash=h},replaceState(a,b,h){location.hash=h}};
    """, """
      await importRouteChanged();assert.equal(location.hash,'#month=2026-06&tab=data');assert.deepEqual(visits,[]);
      location.hash='#month=2026-06&tab=digest';allow=true;await importRouteChanged();assert.deepEqual(visits,['#month=2026-06&tab=digest']);
    """)


def test_import_cleanup_stays_successful_when_refresh_fails():
    run_js("function hasUnfinishedImport(", "function dropFile(", """
      let importStep=3,lastImport={datasetId:7},kpiSaving=false,importLeaving=false;
      const els={};const $=id=>(els[id]??={style:{},innerHTML:''});
      const modalConfirm=async()=>true,api=async()=>{},closeSidebar=()=>{},updateKpiSelection=()=>{},toast=()=>{},esc=String;
      const resetImport=()=>{lastImport=null;importStep=1},loadPeriods=async()=>{throw Error('Offline')};
    """, """
      assert.equal(await leaveImport(),true);assert.equal(lastImport,null);assert.equal(importStep,1);
      assert.match($('import-banner').innerHTML,/deleted.*Offline.*Retry/);
    """)


def test_palette_metric_resolves_refreshed_facts_after_import_cleanup():
    run_js("function paletteActions(", "function openPalette(", """
      const currentUser={role:'analyst'},CI={};let currentFacts=[{metric:'Revenue',has_data:true}],shownFacts=currentFacts;
      const MONTH_TABS=[['kpis','KPIs','dashboard']],PLACES=[['analyze','analytics','#analyze','Analyze']],allPeriods=['2026-06'];
      const currentPeriod='2026-06',canReview=()=>true,fmtPeriod=p=>p,fmtPeriodTitle=p=>p,selectClose=()=>{};
      let opened=null;const nav=()=>{},generateAll=()=>{},exportPDF=()=>{},exportPPTX=()=>{},toggleTheme=()=>{},requestImportView=()=>{};
      const requestImportAction=async action=>{currentFacts=[{metric:'Revenue',has_data:true}];shownFacts=currentFacts;return action()};
      const openDetail=i=>opened=shownFacts[i].metric;
    """, """
      await paletteActions().find(a=>a.group==='Metrics').run();assert.equal(opened,'Revenue');
    """)


def test_import_picker_loading_error_and_empty_states_allow_safe_retry():
    run_js("async function loadKpiPicker(", "async function confirmKpis(", """
      const upload={upload_id:'upload-7'},lastImport={datasetId:7},els={},states=[];
      let kpiLoading=false,response=null;const $=id=>(els[id]??={innerHTML:'',textContent:''});
      const esc=s=>String(s).replaceAll('<','&lt;').replaceAll('>','&gt;');
      const updateKpiSelection=()=>states.push(kpiLoading);
      const api=async p=>{assert.equal(p,'/ingest/upload-7/metrics');if(response===null)throw Error('<Unavailable>');return response};
    """, """
      await loadKpiPicker();assert.deepEqual(states,[true,false]);
      assert.match($('kpi-rows').innerHTML,/&lt;Unavailable&gt;.*Retry/);assert.equal(kpiLoading,false);
      response=[];await loadKpiPicker();assert.match($('kpi-rows').innerHTML,/No KPIs.*upload a file/);
    """)


def run_js(start, end, setup, assertions):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for UI logic tests")
    source = SOURCE.read_text(encoding="utf-8")
    code = source[source.index(start):source.index(end, source.index(start))]
    script = "const assert=require('node:assert/strict');\n" + setup + "\n" + code
    script += "\n(async()=>{" + assertions + "})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_generic_503_is_rejected_not_returned_as_success():
    run_js("async function api(", "const fmt=", """
      let authToken=null,demoSession=false,activeWorkspaceId=null,activeDatasetId=null;
      const fetch=async()=>({ok:false,status:503,statusText:'Unavailable',json:async()=>({detail:'Service unavailable'})});
    """, "await assert.rejects(api('/facts'), /Service unavailable/);")


def test_facts_only_generation_fallback_remains_supported():
    run_js("async function api(", "const fmt=", """
      let authToken=null,demoSession=false,activeWorkspaceId=null,activeDatasetId=null;
      const fallback={metric:'Revenue',period:'2025-01',value:100,faithfulness:'unchecked',narrative:null};
      const fetch=async()=>({ok:false,status:503,json:async()=>fallback});
    """, "assert.equal((await api('/generate-insight')).faithfulness,'unchecked');")


def test_empty_dataset_clears_old_client_facts():
    run_js("async function loadPeriods(", "function skeletonCards", """
      let currentPeriod='2025-01',currentFacts=[{metric:'Old'}],shownFacts=[{metric:'Old'}],detailFact={metric:'Old'};
      const granularity='month',loadDatasets=async()=>{},api=async()=>[];
      let allPeriods=null,overviewFor='x',boardPackFor='x';const renderCloses=()=>{},renderMonthHead=()=>{};
      const elements={}; const $=id=>(elements[id]??={style:{},classList:{remove(){}}});
    """, """
      await loadPeriods(); assert.equal(currentPeriod,null); assert.deepEqual(currentFacts,[]);
      assert.deepEqual(shownFacts,[]); assert.equal(detailFact,null);
    """)


def test_pdf_export_keeps_failed_faithfulness_warning():
    run_js("function buildReportHTML(", "function exportPDF", """
      const currentPeriod='2025-01',BRAND={green:'#123',red:'#456'};
      const priorPeriodLabel=()=>'vs last month',capFirst=s=>s.charAt(0).toUpperCase()+s.slice(1);
      const catColor=()=>'',esc=s=>String(s),fmt=()=>'$100',reportDelta=()=>'',pct=()=>'';
    """, """
      const result=buildReportHTML([{has_data:true,metric:'Revenue',value:100,unit:'USD',
        deltas:{},narrative:'Unverified claim',faithfulness:'failed'}]);
      assert.match(result,/needs review|unverified/i);
      assert.match(result,/numbers|faithfulness/i);
    """)


def test_powerpoint_export_keeps_failed_faithfulness_warning():
    run_js("async function exportPPTX(", "/* ---------- command palette", """
      const text=[],currentPeriod='2025-01',BRAND={green:'#123456',greenDark:'#456',red:'#654321'},activeDatasetObj=null;
      const priorPeriodLabel=()=>'vs last month',capFirst=s=>s.charAt(0).toUpperCase()+s.slice(1);
      const fmtPeriodTitle=p=>p,fmtDate=d=>d,plural=(n,w)=>n+' '+w;
      const shownFacts=[{has_data:true,metric:'Revenue',value:100,deltas:{},narrative:'Some claim',faithfulness:'failed'}];
      const $=()=>({classList:{remove(){}}}),toast=()=>{},loadPptx=async()=>{},fmt=()=>'$100',pct=()=>'';
      const document={documentElement:{getAttribute:()=>null}};
      class PptxGenJS{constructor(){this.ShapeType={line:'line',rect:'rect'}}
        addSlide(){return {addText(value){text.push(value)},addShape(){},addTable(){}}} async writeFile(){}}
    """, "await exportPPTX(); assert.match(text.join(' '),/numbers need review/i);")
