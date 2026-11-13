"""F18: the KPIs tab can list KPIs as a statement, grouped by category in report order."""

import json
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")

PRE = ("const esc=s=>String(s),fmt=(v,u)=>'$'+v,pct=v=>(v>0?'+':'')+v+'%',deltaText=(f,k)=>null,"
       "priorPeriodLabel=()=>'vs last month',localStorage={getItem:()=>null,setItem(){}},"
       "document={body:{classList:{toggle(){}}}},$=()=>null;let shownFacts=[];")


def _html(facts):
    s = JS.index("/* ---- Statement view:")
    e = JS.index("function openFromStatement(", s)
    script = (PRE + JS[s:e] + f"\nshownFacts={json.dumps(facts)};"
              "console.log(JSON.stringify(statementHtml(shownFacts)));")
    try:
        out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True,
                             encoding="utf-8", check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return json.loads(out.stdout)


def _fact(metric, category, value=100, var_abs=10, var_pct=11.1, mom=2.0, **kw):
    return {"metric": metric, "category": category, "has_data": True, "value": value, "unit": "USD",
            "direction_good": "up", "deltas": {"budget_var_abs": var_abs, "budget_var_pct": var_pct, "mom_pct": mom}, **kw}


def test_categories_follow_report_order_and_uncategorized_is_last():
    html = _html([_fact("Opex", "Cost"), _fact("Odd", "Uncategorized"), _fact("Misc", "Zeta"),
                  _fact("Revenue", "Revenue"), _fact("Cash", "Balance Sheet")])
    order = [html.index(f">{c}<") for c in ("Revenue", "Cost", "Balance Sheet", "Zeta", "Uncategorized")]
    assert order == sorted(order)


def test_row_shows_actual_plan_and_variances_and_no_fake_totals():
    html = _html([_fact("Revenue", "Revenue", value=110, var_abs=10, var_pct=10.0, mom=-2.0)])
    assert "$110" in html and "$100" in html          # actual, and plan = actual - variance
    assert "+10%" in html and "c-ok" in html and "-2%" in html and "c-exc" in html
    assert "aren't added up" in html and "Total" not in html


def test_kpi_without_data_or_plan_shows_dashes():
    html = _html([_fact("New", "Revenue", var_abs=None, var_pct=None), {"metric": "Empty", "category": "Revenue", "has_data": False}])
    assert html.count("–") >= 4 and "—" in html
