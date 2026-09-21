"""Run actual standalone UI functions in Node with controlled browser boundaries."""

import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "ui/web/app.js"


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
      let authToken=null,demoSession=false,activeWorkspaceId=null;
      const fetch=async()=>({ok:false,status:503,statusText:'Unavailable',json:async()=>({detail:'Service unavailable'})});
    """, "await assert.rejects(api('/facts'), /Service unavailable/);")


def test_facts_only_generation_fallback_remains_supported():
    run_js("async function api(", "const fmt=", """
      let authToken=null,demoSession=false,activeWorkspaceId=null;
      const fallback={metric:'Revenue',period:'2025-01',value:100,faithfulness:'unchecked',narrative:null};
      const fetch=async()=>({ok:false,status:503,json:async()=>fallback});
    """, "assert.equal((await api('/generate-insight')).faithfulness,'unchecked');")


def test_empty_dataset_clears_old_client_facts():
    run_js("async function loadPeriods(", "function skeletonCards", """
      let currentPeriod='2025-01',currentFacts=[{metric:'Old'}],shownFacts=[{metric:'Old'}],detailFact={metric:'Old'};
      const granularity='month',loadDatasets=async()=>{},api=async()=>[];
      const elements={}; const $=id=>(elements[id]??={style:{},classList:{remove(){}}});
    """, """
      await loadPeriods(); assert.equal(currentPeriod,null); assert.deepEqual(currentFacts,[]);
      assert.deepEqual(shownFacts,[]); assert.equal(detailFact,null);
    """)


def test_pdf_export_keeps_failed_faithfulness_warning():
    run_js("function buildReportHTML(", "function exportPDF", """
      const currentPeriod='2025-01',BRAND={green:'#123',red:'#456'};
      const catColor=()=>'',esc=s=>String(s),fmt=()=>'$100',reportDelta=()=>'',pct=()=>'';
    """, """
      const result=buildReportHTML([{has_data:true,metric:'Revenue',value:100,unit:'USD',
        deltas:{},narrative:'Unverified claim',faithfulness:'failed'}]);
      assert.match(result,/needs review|unverified/i);
      assert.match(result,/numbers|faithfulness/i);
    """)


def test_powerpoint_export_keeps_failed_faithfulness_warning():
    run_js("async function exportPPTX(", "/* ---------- command palette", """
      const text=[],currentPeriod='2025-01',BRAND={green:'#123',greenDark:'#456'};
      const shownFacts=[{has_data:true,metric:'Revenue',value:100,deltas:{},narrative:'Some claim',faithfulness:'failed'}];
      const $=()=>({classList:{remove(){}}}),toast=()=>{},loadPptx=async()=>{},fmt=()=>'$100',pct=()=>'';
      const document={documentElement:{getAttribute:()=>null}};
      class PptxGenJS{addSlide(){return {addText(value){text.push(value)}}} async writeFile(){}}
    """, "await exportPPTX(); assert.match(text.join(' '),/numbers need review/i);")
