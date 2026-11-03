"""UI regressions from the Oct-2026 QA pass (QA_REVIEW_2026-10.md, P0 #1, #2, #7),
run against the real app.js functions in Node via the shared harness."""

from test_frontend_followup import run_js


def test_redundant_measure_starts_ignored():
    # P0 #2: amount_local duplicates amount_usd — keeping both doubled every
    # GL metric and summed EUR with USD.
    run_js("function roleFor(", "const ROLES=", "", """
      assert.equal(roleFor({guessed_role:'measure',redundant_with:'amount_usd'}),'ignore');
      assert.equal(roleFor({guessed_role:'measure',redundant_with:null}),'measure');
      // redundant_with only demotes measures; other roles are left alone.
      assert.equal(roleFor({guessed_role:'dimension',redundant_with:'x'}),'dimension');
    """)


_CONFIRM_KPIS_SETUP = """
  const calls=[];
  const els={'imp-name':{value:'Marketing Funnel QA'},'imp-domain':{value:'marketing'},
             'import-banner':{style:{},className:'',innerHTML:''}};
  const $=id=>(els[id]??={style:{},value:''});
  const included={checked:true},fields={'.k-include':included,'.k-name':{value:'Revenue'},
    '.k-cat':{value:'Sales'},'.k-unit':{value:'USD'},'.k-dir':{value:'up'}};
  const rows=[{dataset:{src:'revenue_usd'},querySelector:s=>fields[s]}];
  const document={querySelectorAll:()=>rows};
  let kpiSaving=false,importLeaving=false;const updateKpiSelection=()=>{},esc=String;
  const api=async(path,opts={})=>{calls.push([opts.method||'GET',path,opts.body||null]);return {};};
  const resetImport=()=>{},loadPeriods=async()=>{},nav=()=>{},plural=(n,w)=>n+' '+w;
  const toasts=[];const toast=m=>toasts.push(m);
"""


def test_finish_import_sends_only_included_kpis_and_rejects_empty_selection():
    run_js("async function confirmKpis(", "// Entry point", _CONFIRM_KPIS_SETUP + """
      let lastImport={rows:2,metrics:2,periods:1,datasetId:5};
      rows.push({dataset:{src:'excluded'},querySelector:s=>s==='.k-include'?{checked:false}:fields[s]});
    """, """
      importLeaving=true;await confirmKpis();assert.deepEqual(calls,[]);importLeaving=false;
      included.checked=false;await confirmKpis();assert.deepEqual(calls,[]);
      included.checked=true;await confirmKpis();
      assert.deepEqual(JSON.parse(calls.find(c=>c[1]==='/kpis')[2]).kpis,[{
        source_metric:'revenue_usd',display_name:'Revenue',category:'Sales',unit:'USD',direction_good:'up'}]);
    """)


def test_finish_import_renames_the_new_dataset_not_the_previous_one():
    # P0 #1: activeDatasetId still pointed at the finance dataset (4) while
    # the import had just created dataset 5, so finance got renamed.
    run_js("async function confirmKpis(", "// Entry point", _CONFIRM_KPIS_SETUP + """
      let activeDatasetId=4;
      let lastImport={rows:1278,metrics:80,periods:18,datasetId:5};
    """, """
      await confirmKpis();
      const patches=calls.filter(c=>c[0]==='PATCH');
      assert.equal(patches.length,1);
      assert.equal(patches[0][1],'/datasets/5');
      assert.deepEqual(JSON.parse(patches[0][2]),{name:'Marketing Funnel QA',domain:'marketing'});
      assert.ok(!calls.some(c=>c[1]==='/datasets/4'));
    """)


def test_first_import_still_saves_name_when_no_dataset_was_active():
    # Same bug, first-import flavour: activeDatasetId was null so the chosen
    # name/domain were silently dropped.
    run_js("async function confirmKpis(", "// Entry point", _CONFIRM_KPIS_SETUP + """
      let activeDatasetId=null;
      let lastImport={rows:468,metrics:27,periods:18,datasetId:3};
    """, """
      await confirmKpis();
      assert.ok(calls.some(c=>c[0]==='PATCH'&&c[1]==='/datasets/3'));
    """)


def test_local_dev_boot_reveals_the_app():
    # P0 #7: with AUTH_ENABLED=false #app-root stayed display:none (blank page).
    run_js("async function boot(", "/* ================= Google Sign-In", """
      const order=[];let appCfg;
      const fetch=async()=>({json:async()=>({auth_enabled:false,demo_enabled:false})});
      const initSentry=()=>{},$=()=>null;
      const hideLogin=()=>order.push('hideLogin'),startApp=async()=>order.push('startApp');
    """, """
      await boot();
      assert.deepEqual(order,['hideLogin','startApp']);
    """)
