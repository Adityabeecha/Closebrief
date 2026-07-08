"""Period aggregation rules (v3.0): flow=SUM, balance=LAST, ratio=unavailable."""

from app.compute.aggregate import (
    aggregate_series,
    infer_aggregation_type,
    period_key,
    prior_key,
    year_ago_key,
)

MONTHS = [
    {"period": "2025-01", "value": 100.0, "budget": 90.0},
    {"period": "2025-02", "value": 110.0, "budget": 95.0},
    {"period": "2025-03", "value": 120.0, "budget": 100.0},
]


def test_quarter_revenue_is_sum_of_months():
    out = aggregate_series(MONTHS, "quarter", "flow")
    assert out["2025-Q1"]["value"] == 330.0          # 100 + 110 + 120
    assert out["2025-Q1"]["budget"] == 285.0          # 90 + 95 + 100
    assert out["2025-Q1"]["months"] == 3


def test_year_flow_is_sum():
    out = aggregate_series(MONTHS, "year", "flow")
    assert out["2025"]["value"] == 330.0


def test_balance_metric_takes_last_month():
    balances = [
        {"period": "2025-01", "value": 500.0, "budget": None},
        {"period": "2025-02", "value": 480.0, "budget": None},
        {"period": "2025-03", "value": 520.0, "budget": None},
    ]
    out = aggregate_series(balances, "quarter", "balance")
    assert out["2025-Q1"]["value"] == 520.0           # last month, not the sum


def test_ratio_quarter_is_not_mean_of_monthly_percents():
    pcts = [
        {"period": "2025-01", "value": 60.0, "budget": None},
        {"period": "2025-02", "value": 62.0, "budget": None},
        {"period": "2025-03", "value": 64.0, "budget": None},
    ]
    out = aggregate_series(pcts, "quarter", "ratio")
    # A quarter margin % is NOT the mean (62.0) — it's marked unavailable, never averaged.
    assert out["2025-Q1"]["unavailable"] is True
    assert out["2025-Q1"]["value"] != 62.0
    assert out["2025-Q1"]["value"] is None


def test_ratio_single_month_passes_through():
    out = aggregate_series(MONTHS[:1], "month", "ratio")
    assert out["2025-01"]["value"] == 100.0
    assert out["2025-01"]["unavailable"] is False


def test_infer_aggregation_type():
    assert infer_aggregation_type("Net Revenue", "USD") == "flow"
    assert infer_aggregation_type("Gross Margin %", "%") == "ratio"
    assert infer_aggregation_type("Cash Balance", "USD") == "balance"
    assert infer_aggregation_type("Cash Runway", "USD") == "balance"


def test_period_key_forms():
    assert period_key("2025-03", "month") == "2025-03"
    assert period_key("2025-03", "quarter") == "2025-Q1"
    assert period_key("2025-11", "quarter") == "2025-Q4"
    assert period_key("2025-07", "year") == "2025"


def test_prior_and_year_ago_keys():
    assert prior_key("2025-Q1", "quarter") == "2024-Q4"
    assert prior_key("2025-01", "month") == "2024-12"
    assert prior_key("2025", "year") == "2024"
    assert year_ago_key("2025-Q3", "quarter") == "2024-Q3"
    assert year_ago_key("2025-03", "month") == "2024-03"
