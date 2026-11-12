"""Phase 2 screens: fiscal quarters and years are labelled as fiscal (F12), and
the import receipt shows what happened plus per-KPI totals (F7)."""

import json
import subprocess
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")
MONTHS = 'const MONTHS=["January","February","March","April","May","June","July","August","September","October","November","December"];'


def _js(expr, start, end, prelude=""):
    s = SRC.index(start)
    e = SRC.index(end, s)
    try:
        out = subprocess.run(["node", "-e", prelude + SRC[s:e] + f"\nconsole.log(JSON.stringify({expr}));"],
                             capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return json.loads(out.stdout.strip())


SPAN = ("// The active dataset's fiscal year start", "// One date format everywhere")


def test_calendar_year_labels_are_unchanged():
    pre = MONTHS + "let activeDatasetObj={fiscal_year_start:1};"
    assert _js('[fmtPeriod("2026-Q1"),fmtPeriod("2026"),fmtPeriodTitle("2026-Q1"),fmtPeriod("2026-03","long")]',
               *SPAN, pre) == ["Q1 2026", "2026", "Q1 2026 overview", "March 2026"]


def test_fiscal_quarters_and_years_say_FY():
    pre = MONTHS + "let activeDatasetObj={fiscal_year_start:4};"
    assert _js('[fmtPeriod("2026-Q1"),fmtPeriod("2026-Q1","axis"),fmtPeriod("2026"),fmtPeriodTitle("2026-Q4"),'
               'fmtPeriodTitle("2026"),fmtPeriod("2026-03")]', *SPAN, pre) == \
        ["Q1 FY2026", "Q1 FY26", "FY2026", "Q4 FY2026 overview", "FY2026 overview", "Mar 2026"]


def test_receipt_shows_what_happened_and_totals():
    # The app groups digits in the viewer's own locale; pin one so the test is machine-independent.
    pre = ("const _tl=Number.prototype.toLocaleString;Number.prototype.toLocaleString=function(l,o){return _tl.call(this,'en-US',o);};"
           "const esc=s=>String(s).replace(/</g,'&lt;'),plural=(n,w)=>n+' '+w+(n===1?'':'s'),"
           "fmtPeriod=p=>p,fmtWhen=s=>String(s).slice(0,10),fmtDate=fmtWhen;"
           + SRC[SRC.index("function importReportHtml("):SRC.index("function resetImport(")])
    html = _js('receiptHtml({kind:"budget_join",filename:"plan.csv",created_at:"2026-11-12T10:00:00Z",'
               'report:{rows_read:5,rows_kept:4,totals_dropped:[{row:5,label:"Total Opex"}]},'
               'kpi_totals:[{metric:"Opex",periods:3,value_total:600000.5,budget_total:null,rows:3}]})',
               "const RECEIPT_KIND=", "async function loadReceipts(", pre)
    assert "Budget added · plan.csv · 2026-11-12" in html
    assert "5 rows read, 4 kept" in html and "Total Opex" in html
    assert "<td>Opex</td>" in html and "600,000.5" in html and ">—<" in html
