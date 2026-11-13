"""Phase 5 screen notes: at a glance can show every mover, a reason opened from a KPI
starts from its movement, and quarter/year views say how they are made."""

import json
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")
HTML = (Path(__file__).resolve().parents[1] / "ui" / "web" / "index.html").read_text(encoding="utf-8")


def _node(script):
    try:
        out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True,
                             encoding="utf-8", check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return json.loads(out.stdout)


def test_reason_starts_from_the_movement():
    s = JS.index("// The note's first line:")
    e = JS.index("// Opens the close's Context tab", s)
    pre = ("const fmt=(v,u)=>'$'+v,fmtPeriod=()=>'March 2026',priorPeriodLabel=()=>'vs last month',"
           "deltaText=(f,k)=>k==='budget'?'+1.2%':'+1.6%';let currentPeriod='2026-03';")
    out = _node(pre + JS[s:e] + """
console.log(JSON.stringify([
  reasonStarter({metric:'Opex',has_data:true,value:1090,unit:'USD',deltas:{budget_var_abs:12800}}),
  reasonStarter({metric:'Opex',has_data:true,value:1090,unit:'USD',deltas:{budget_var_abs:null,mom_pct:1.6}}),
  reasonStarter({metric:'Opex',has_data:false}),reasonStarter(undefined)]));""")
    assert out[0] == "Opex was $1090 in March 2026, +1.2% vs plan (+$12800). Reason: "
    assert out[1] == "Opex was $1090 in March 2026, +1.6% vs last month. Reason: "
    assert out[2] == "" and out[3] == ""


def test_glance_offers_show_all_beyond_four_movers():
    assert "if(!glanceAll)movers.splice(4);" in JS
    assert 'Show all ${allMovers}' in JS and "Show the top 4" in JS


def test_quarter_and_year_views_carry_a_rollup_note():
    assert '<p class="rollup-note" id="rollup-note" hidden></p>' in HTML
    assert "Commentary is written per month; this ${granularity===\"year\"?\"year\":\"quarter\"} view adds the months up" in JS
    assert 'note.hidden=granularity==="month"' in JS


def test_digest_send_names_its_recipients():
    s = JS.index("// Who a send reaches, in words.")
    e = JS.index("// Sending is the one step", s)
    out = _node(JS[s:e] + """
console.log(JSON.stringify([recipientsText(null),recipientsText([]),
  recipientsText([{channel:'email',label:'cfo@x.co, ceo@x.co'},{channel:'slack',label:'Slack webhook (…9876)'}])]));""")
    assert "every channel" in out[0]
    assert "nothing is delivered" in out[1] and "only marks the close as sent" in out[1]
    assert out[2] == "It goes to: Email: cfo@x.co, ceo@x.co; Slack webhook (…9876)."
    assert 'okLabel:to&&!to.length?"Mark as sent":"Send digest"' in JS


def test_excel_download_is_offered_where_the_other_exports_are():
    assert HTML.count("exportExcel(") == 2
    assert "/close/export.xlsx?period=" in JS and "{raw:true}" in JS and "if(opts.raw)return r;" in JS
