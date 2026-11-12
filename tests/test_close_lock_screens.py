"""Lock this close and What changed, on the Overview (F9 screens)."""

import json
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "ui" / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")

PRE = ("const esc=s=>String(s).replace(/</g,'&lt;'),plural=(n,w)=>n+' '+w+(n===1?'':'s'),"
       "fmtDate=s=>String(s).slice(0,10),fmtPeriod=(p,f)=>p==='2025-12'?'December 2025':p,"
       "fmt=(v,u)=>'$'+v;let granularity='month',currentFacts=[{metric:'Revenue',unit:'USD'}],"
       "overviewStatus=null,overviewChanges=null,currentPeriod='2025-12';"
       "const els={};const $=id=>(els[id]??={style:{},innerHTML:'',textContent:''});")


def _js(expr):
    s = JS.index("// Lock this close: after the digest is sent")
    e = JS.index("function openMetricByName(", s)
    skipped = JS[JS.index("function lockedSkippedText("):JS.index("function importReportHtml(")]
    try:
        out = subprocess.run(["node", "-e", PRE + skipped + JS[s:e] + f"\nconsole.log(JSON.stringify({expr}));"],
                             capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return json.loads(out.stdout)


def test_lock_offered_only_once_sent_and_unlock_when_locked():
    html = _js("""(()=>{const r=[];
      overviewStatus={period:'2025-12',status:'in_progress',locked:false};paintLock();r.push($('ov-lock').style.display);
      overviewStatus={period:'2025-12',status:'shared',locked:false,changes_since_shared:2};paintLock();r.push($('ov-lock').innerHTML);
      overviewStatus={period:'2025-12',status:'shared',locked:true,locked_at:'2026-11-12T10:00:00Z',locked_by:'a@b.co'};paintLock();
      r.push($('ov-lock').innerHTML);return r;})()""")
    assert html[0] == "none"
    assert "Lock this close" in html[1] and "2 figures changed after this close was sent" in html[1]
    assert "<b>Locked</b> on 2026-11-12 by a@b.co" in html[2] and "Unlock" in html[2] and "Lock this close" not in html[2]


def test_changes_read_old_to_new_with_source():
    lines = _js("""[changeLine({metric:'Revenue',period:'2025-12',field:'budget',old_value:5,new_value:4,
        source:'budget_join',actor:'a@b.co',at:'2026-11-12T10:00:00Z'}),
      changeLine({metric:'Revenue',period:'2025-12',field:'value',old_value:null,new_value:7,source:'sync',at:'2026-11-12'}),
      changeLine({metric:'Revenue',period:'2025-12',field:'value',old_value:3,new_value:null,source:'import',at:'2026-11-12'})]""")
    assert "budget $5 → $4 · Budget added · a@b.co" in lines[0]
    assert "added $7 · Sheet sync" in lines[1]
    assert "removed (was $3) · Import" in lines[2]


def test_locked_rows_are_reported_on_imports():
    text = _js("lockedSkippedText([{period:'2025-12',rows:3}])")
    assert "3 rows not applied" in text and "December 2025" in text and "Unlock" in text
    assert _js("lockedSkippedText([])") == ""


def test_locked_close_hides_commentary_controls():
    assert ".close-locked #gen-all,.close-locked #d-regen,.close-locked #d-rev-strip .write-only{display:none!important}" in HTML
    assert 'locked:["Locked","locked"]' in JS
