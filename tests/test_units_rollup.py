"""Phase 2: the roll-up into quarters is shown and editable (F10); Number and
Days units are formatted everywhere, a days KPI moves in days, and "47 days"
for a days KPI is checked as a figure, not skipped as a duration (F22); the
close says how fresh its data is (F21)."""

import subprocess
from pathlib import Path

import pytest

from app.board_pack import _delta
from app.board_pack import _fmt as pack_fmt
from app.generation.guard import check_faithfulness
from app.generation.prompts import _fmt as prompt_fmt
from app.generation.prompts import build_user_prompt
from app.kpis.library import suggest_import_defaults
from app.schemas import ComputedFact

DSO = ComputedFact(metric="DSO", category="Working capital", period="2025-12", value=47.0, prior_value=44.0,
                   unit="days", deltas={"mom_pct": 6.8182, "budget_var_abs": 2.0, "budget_var_pct": 4.4444},
                   trend="up", is_anomaly=False)
SRC = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")


def _js(expr, start, end, prelude=""):
    s, e = SRC.index(start), SRC.index(end, SRC.index(start))
    try:
        out = subprocess.run(["node", "-e", prelude + SRC[s:e] + f"\nconsole.log(JSON.stringify({expr}));"],
                             capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return out.stdout.strip()


def test_suggestions_say_how_a_kpi_rolls_up():
    assert suggest_import_defaults("Net Revenue")["aggregation_type"] == "flow"
    assert suggest_import_defaults("Cash Balance")["aggregation_type"] == "balance"
    assert suggest_import_defaults("Gross Margin %")["aggregation_type"] == "ratio"


def test_rollup_follows_the_unit_unless_the_user_chose():
    span = ('// Units a KPI can have', 'function onUnitChange(')
    assert _js('[rollupForUnit("%","flow"),rollupForUnit("days","balance"),rollupForUnit("USD","balance"),'
               'rollupForUnit("number","ratio")]', *span, prelude="const esc=String;") == \
        '["ratio","ratio","balance","flow"]'


def test_number_and_days_are_formatted_everywhere():
    fmt_span = ('// unit "USD" means "an amount of money"', "const pct=")
    assert _js('[fmt(1234.567,"number"),fmt(47,"days"),fmt(1,"days"),fmt(-2.25,"days")]', *fmt_span) == \
        '["1,234.57","47 days","1 day","−2.3 days"]'
    assert pack_fmt(1234.567, "number") == "1,234.57" and pack_fmt(47.0, "days") == "47 days"
    assert prompt_fmt(47.0, "days") == "47 days" and prompt_fmt(-2.25, "number") == "−2.25"


def test_a_days_kpi_moves_in_days():
    f = {"metric": "DSO", "unit": "days", "value": 47.0, "prior_value": 44.0,
         "deltas": {"mom_pct": 6.8182, "budget_var_abs": 2.0, "budget_var_pct": 4.4444}}
    assert _delta(f, "budget") == "+2.0 days" and _delta(f, "mom") == "+3.0 days"
    span = ("const pct=v=>", "function datasetDisplayName(")
    assert _js('deltaText({unit:"days",value:47,prior_value:44,deltas:{mom_pct:6.8,budget_var_abs:2,budget_var_pct:4.4}},"mom")',
               *span) == '"+3.0 days"'
    prompt = build_user_prompt(DSO, [])
    assert "budget_variance: +2 days" in prompt and "month_over_month_change: +3 days" in prompt
    assert "4.4%" not in prompt


def test_days_figures_are_checked_not_skipped_as_durations():
    assert check_faithfulness("DSO rose to 47 days, 3 days more than last month and 2 days over plan.", DSO)[0]
    # Before, "52 days" read as a duration and slipped through unchecked.
    assert not check_faithfulness("DSO rose to 52 days.", DSO)[0]


def test_close_header_says_how_fresh_the_data_is():
    span = ("function dataAsOf(", "function rollupNextStep(")
    pre = "const fmtDate=s=>String(s).slice(0,10);let activeDatasetObj={created_at:'2026-11-01',data_changed_at:'2026-11-09'};"
    assert _js("dataAsOf()", *span, prelude=pre) == '"Data as of 2026-11-09"'
    assert _js("(activeDatasetObj={created_at:'2026-11-01'},dataAsOf())", *span, prelude=pre) == '"Data as of 2026-11-01"'
    assert _js("(activeDatasetObj=null,dataAsOf())", *span, prelude=pre) == "null"


@pytest.mark.parametrize("name,unit", [("DSO", "days"), ("DSO days", "days"), ("Days Payable Outstanding", "days"),
                                       ("DPO", "days"), ("Holidays booked", "USD"), ("Net Revenue", "USD")])
def test_day_counts_are_recognised_by_name(name, unit):
    assert suggest_import_defaults(name)["unit"] == unit
