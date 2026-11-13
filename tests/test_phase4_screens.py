"""Phase 4 screens: contributors under a KPI (F5), forecast (F19) and year to date (F8)."""

import json
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")
HTML = (Path(__file__).resolve().parents[1] / "ui" / "web" / "index.html").read_text(encoding="utf-8")

PRE = ("const esc=s=>String(s),fmt=(v,u)=>'$'+v,pct=v=>(v>0?'+':v<0?'-':'')+Math.abs(v).toFixed(1)+'%',"
       "plural=(n,w)=>n+' '+w+(n===1?'':'s'),capFirst=s=>s.charAt(0).toUpperCase()+s.slice(1);"
       "let activeDatasetObj={fiscal_year_start:1};const fiscalStart=()=>activeDatasetObj.fiscal_year_start;")


def _js(expr):
    s = JS.index("/* ---- Year to date (F8) ---- */")
    e = JS.index("function metricHash(", s)
    try:
        out = subprocess.run(["node", "-"], input=PRE + JS[s:e] + f"\nconsole.log(JSON.stringify({expr}));",
                             capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return json.loads(out.stdout)


R = {"available": True, "dimension": "department", "unit": "USD",
     "members": [{"member": "R&D", "value": 384, "budget": 410, "var_abs": -26},
                 {"member": "Sales", "value": 512, "budget": 486, "var_abs": 26},
                 {"member": "G&A", "value": 192, "budget": 179, "var_abs": 13}],
     "total": {"value": 1088, "budget": 1075, "var_abs": 13}}


def test_contributors_share_is_of_all_movement_so_it_stays_within_100():
    html = _js(f"contributorsHtml({json.dumps(R)},{{direction_good:'down'}})")
    assert "<th>Department</th>" in html and "Share of movement" in html
    assert ">40%<" in html and ">20%<" in html and "200%" not in html and "-40%" not in html
    assert "c-ok" in html and "c-exc" in html          # cost KPI: under plan is good


def test_contributors_explain_why_when_unavailable():
    html = _js("contributorsHtml({available:false,reason:\"A percentage can't be split into contributors.\"},{})")
    assert "can't be split" in html and "<table" not in html


def test_contributors_without_plan_show_actuals_only():
    r = {**R, "members": [{"member": "A", "value": 5, "budget": None, "var_abs": None}],
         "total": {"value": 5, "budget": None, "var_abs": None}}
    html = _js(f"contributorsHtml({json.dumps(r)},{{}})")
    assert "Plan" not in html and "Share" not in html and "$5" in html


def test_year_to_date_figures_and_fiscal_label():
    figs = _js("ytdFigs({unit:'USD',ytd:{months:3,value:3213,budget:3175,budget_months:3,var_pct:1.2,yoy_pct:23.5}})")
    assert figs[0] == ["Year to date (3 months)", "$3213"] and figs[2] == ["YTD vs plan", "+1.2%"]
    partial = _js("ytdFigs({unit:'USD',ytd:{months:9,value:9,budget:null,budget_months:7,var_pct:null,yoy_pct:null}})")
    assert partial[1] == ["YTD plan", "— (budget for 7 of 9 months)"] and partial[3][1] == "—"
    assert _js("(activeDatasetObj.fiscal_year_start=4,ytdLabel())") == "Fiscal year to date"
    assert _js("ytdFigs({ytd:null})") == []


def test_forecast_is_a_mapping_role_and_a_join_option():
    assert '"budget","forecast","id"' in JS and 'forecast:"Latest forecast"' in JS
    assert 'forecast_col:by("forecast")[0]||null' in JS
    assert '<option value="forecast">Add a forecast to an existing dataset</option>' in HTML
    assert "field:joinWord()" in JS
