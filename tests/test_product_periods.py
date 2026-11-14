"""Quarter/year ratio averages, baselines, charts, and recorded reasons."""

import pytest
from test_datasets import client  # noqa: F401
from test_e2e_analyst_fixes import BUDGET, PL, _note
from test_import_honesty import _import

from app.compute.aggregate import aggregate_series


@pytest.mark.parametrize("granularity,key", [("quarter", "2026-Q4"), ("year", "2026")])
def test_ratio_averages_available_actual_budget_and_forecast(granularity, key):
    rows = [{"period": "2026-10", "value": 60, "budget": 50, "forecast": 58},
            {"period": "2026-11", "value": None, "budget": None, "forecast": None},
            {"period": "2026-12", "value": 80, "budget": 70, "forecast": 78}]
    r = aggregate_series(rows, granularity, "ratio")[key]
    assert (r["value"], r["budget"], r["forecast"], r["unavailable"]) == (70, 60, 68, False)


def test_ratio_baselines_average_only_their_non_null_observations():
    rows = [{"period": "2026-10", "value": 60, "budget": None, "forecast": 0},
            {"period": "2026-11", "value": 80, "budget": 50, "forecast": None}]
    r = aggregate_series(rows, "quarter", "ratio")["2026-Q4"]
    assert (r["value"], r["budget"], r["forecast"]) == (70, 50, 0)


def test_ratio_all_null_observations_stay_missing():
    r = aggregate_series([{"period": "2026-10", "value": None, "budget": None, "forecast": None}],
                         "quarter", "ratio")["2026-Q4"]
    assert (r["value"], r["budget"], r["forecast"]) == (None, None, None)


@pytest.mark.parametrize("granularity,key", [("quarter", "2026-Q4"), ("year", "2026")])
@pytest.mark.parametrize("unit,agg,value,budget,forecast", [("USD", "flow", 140, 120, 136),
                                                          ("%", "ratio", 70, 60, 68),
                                                          ("USD", "balance", 80, 70, 78)])
def test_period_cards_return_baselines_chart_and_point_variances(client, granularity, key, unit, agg, value, budget, forecast):  # noqa: F811
    _import(client, "period,metric,value,budget,forecast\n2026-10,Measure,60,50,58\n2026-12,Measure,80,70,78\n",
            {**BUDGET, "forecast_col": "forecast"})
    response = client.post("/kpis", json={"kpis": [{"source_metric": "Measure", "display_name": "Measure",
                           "category": "Test", "unit": unit, "direction_good": "up", "aggregation_type": agg}]})
    assert response.status_code == 200, response.text
    (card,) = client.get(f"/facts?period={key}&granularity={granularity}").json()
    assert (card["value"], card["budget"], card["forecast"]) == (value, budget, forecast)
    assert card["deltas"]["budget_var_abs"] == value - budget
    assert card["deltas"]["forecast_var_abs"] == value - forecast
    assert card["chart_data"]["budget_vs_actual"] == {"actual": value, "budget": budget}
    if agg == "ratio":
        assert card["aggregation"] == "average"
    (no_chart,) = client.get(f"/facts?period={key}&granularity={granularity}&include_charts=false").json()
    assert no_chart["chart_data"] is None
    assert card["reason_note"] is None


@pytest.mark.parametrize("granularity,key", [("quarter", "2026-Q4"), ("year", "2026")])
def test_period_reason_is_newest_matching_note_from_any_month(client, granularity, key):  # noqa: F811
    ds = _import(client, PL, BUDGET)["dataset_id"]
    _note(client)
    newest = _note(client, effective_date="2026-11", title="November renewals")
    _note(client, effective_date="2025-11")
    _note(client, metric_tags=[])
    _note(client, metric_tags=["Opex"])
    _import(client, PL, BUDGET)
    _note(client, title="Other dataset")
    # Activate the original dataset before requesting its cards.
    client.post(f"/datasets/{ds}/activate")
    cards = client.get(f"/facts?period={key}&granularity={granularity}").json()
    card = next(c for c in cards if c["metric"] == "Revenue")
    assert card["reason_note"] == {"id": newest["id"], "title": "November renewals"}
