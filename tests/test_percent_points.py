"""F11: a percent KPI (gross margin 38% against a 40% plan) varies by -2.0 points,
not -5.0%. Screen, board pack, digest and the model's input all say points, and
the figure check accepts points in the commentary."""

import subprocess
from pathlib import Path

import pytest

from app.board_pack import _delta
from app.generation.guard import check_faithfulness
from app.generation.prompts import build_user_prompt
from app.schemas import ComputedFact

MARGIN = ComputedFact(metric="Gross Margin", category="Profitability", period="2025-12", value=38.0,
                      prior_value=38.5, unit="%",
                      deltas={"mom_pct": -1.2987, "yoy_pct": 2.7027, "budget_var_abs": -2.0, "budget_var_pct": -5.0},
                      trend="flat", is_anomaly=False)
FACT_DICT = {"metric": "Gross Margin", "unit": "%", "value": 38.0, "prior_value": 38.5,
             "deltas": {"mom_pct": -1.2987, "yoy_pct": 2.7027, "budget_var_abs": -2.0, "budget_var_pct": -5.0}}
REVENUE = {**FACT_DICT, "metric": "Revenue", "unit": "USD", "value": 4200000, "prior_value": 4772727,
           "deltas": {"mom_pct": -12.0, "budget_var_abs": -370000, "budget_var_pct": -8.1}}


def test_board_pack_writes_points_for_percent_kpis_only():
    assert _delta(FACT_DICT, "budget") == "-2.0 pts"
    assert _delta(FACT_DICT, "mom") == "-0.5 pts"
    assert _delta(FACT_DICT, "yoy") == "+1.0 pts"      # 38% now, 37% a year ago
    assert _delta(REVENUE, "budget") == "-8.1%"


def test_model_is_given_points_not_a_relative_change():
    prompt = build_user_prompt(MARGIN, [])
    assert "budget_variance: −2.0pp" in prompt and "month_over_month_change: −0.5pp" in prompt
    assert "-5.0%" not in prompt and "−5.0%" not in prompt and "budget_variance_percent" not in prompt


def test_figure_check_accepts_points_and_refuses_the_relative_change_as_points():
    assert check_faithfulness("Gross margin was 38.0%, 2.0 points below plan and down 0.5 points on last month.", MARGIN)[0]
    assert check_faithfulness("Gross margin was 38.0%, 2.0pp below plan.", MARGIN)[0]
    assert not check_faithfulness("Gross margin was 38.0%, 5.0 points below plan.", MARGIN)[0]


def _js(expr):
    src = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")
    start = src.index("const pct=v=>")
    end = src.index("function datasetDisplayName(", start)
    try:
        out = subprocess.run(["node", "-e", src[start:end] + f"\nconsole.log(JSON.stringify({expr}));"],
                             capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return out.stdout.strip()


def test_screen_writes_points_for_percent_kpis():
    m = 'const m={unit:"%",value:38,prior_value:38.5,deltas:{mom_pct:-1.2987,yoy_pct:2.7027,budget_var_abs:-2,budget_var_pct:-5}};'
    assert _js(f'(()=>{{{m}return [deltaText(m,"budget"),deltaText(m,"mom"),deltaText(m,"yoy")]}})()') == \
        '["−2.0 pts","−0.5 pts","+1.0 pts"]'
    assert _js('deltaText({unit:"USD",deltas:{budget_var_pct:-8.1}},"budget")') == '"−8.1%"'
    assert _js('deltaText({unit:"%",deltas:{}},"budget")') == "null"


def test_digest_items_know_their_unit_and_direction(make_client):  # noqa: F811
    client = make_client(auth_on=False)
    from app.datasets import create_dataset
    from app.db import get_connection
    conn = get_connection()
    ds = create_dataset(conn, "Digest units")
    mid = conn.execute("INSERT INTO metrics (dataset_id, name, unit) VALUES (?, 'Gross Margin', '%')", (ds,)).lastrowid
    conn.execute("INSERT INTO computed_facts (metric_id, period, value, budget_var_abs, budget_var_pct) "
                 "VALUES (?, '2026-06', 38, -2, -5)", (mid,))
    conn.execute("INSERT INTO kpi_configs (dataset_id, source_metric, display_name, unit, direction_good) "
                 "VALUES (?, 'Gross Margin', 'Gross Margin', '%', 'up')", (ds,))
    conn.execute("INSERT INTO generated_reports (metric_id, period, narrative, confidence, faithfulness, review_status) "
                 "VALUES (?, '2026-06', 'Margin slipped. Pricing.', 'High', 'passed', 'approved')", (mid,))
    conn.commit()
    conn.close()
    (item,) = client.get("/digest?period=2026-06").json()["items"]
    assert (item["unit"], item["direction_good"]) == ("%", "up")


from test_auth import make_client  # noqa: E402,F401  (pytest fixture)
