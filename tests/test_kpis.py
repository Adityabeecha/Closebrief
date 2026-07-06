import pandas as pd
import pytest

from app.compute.kpis import compute_kpis


def make_df(rows):
    return pd.DataFrame(rows, columns=["metric", "period", "value", "budget"])


def get_fact(facts: pd.DataFrame, metric: str, period: str) -> dict:
    row = facts[(facts["metric"] == metric) & (facts["period"] == period)]
    assert len(row) == 1, f"expected exactly one fact for {metric} {period}"
    return row.iloc[0].to_dict()


def test_mom_pct_basic():
    df = make_df([
        ("Revenue", "2025-01", 100.0, 100.0),
        ("Revenue", "2025-02", 110.0, 100.0),
    ])
    facts = compute_kpis(df)
    feb = get_fact(facts, "Revenue", "2025-02")
    assert feb["mom_pct"] == pytest.approx(10.0)
    assert feb["prior_value"] == pytest.approx(100.0)


def test_first_period_deltas_are_null_not_error():
    df = make_df([("Revenue", "2025-01", 100.0, 90.0)])
    facts = compute_kpis(df)
    jan = get_fact(facts, "Revenue", "2025-01")
    assert jan["mom_pct"] is None
    assert jan["yoy_pct"] is None
    assert jan["prior_value"] is None
    # budget variance still computes
    assert jan["budget_var_abs"] == pytest.approx(10.0)
    assert jan["budget_var_pct"] == pytest.approx(11.1111, abs=0.01)


def test_yoy_pct():
    df = make_df([
        ("Revenue", "2024-03", 200.0, 200.0),
        ("Revenue", "2025-03", 250.0, 240.0),
    ])
    facts = compute_kpis(df)
    mar25 = get_fact(facts, "Revenue", "2025-03")
    assert mar25["yoy_pct"] == pytest.approx(25.0)
    assert mar25["mom_pct"] is None  # no 2025-02 in the data


def test_budget_variance_negative():
    df = make_df([("Revenue", "2025-01", 92.0, 100.0)])
    facts = compute_kpis(df)
    jan = get_fact(facts, "Revenue", "2025-01")
    assert jan["budget_var_abs"] == pytest.approx(-8.0)
    assert jan["budget_var_pct"] == pytest.approx(-8.0)


def test_missing_budget_gives_null_variance():
    df = make_df([("Revenue", "2025-01", 100.0, None)])
    facts = compute_kpis(df)
    jan = get_fact(facts, "Revenue", "2025-01")
    assert jan["budget_var_abs"] is None
    assert jan["budget_var_pct"] is None


def test_trend_up():
    rows = [("Revenue", f"2025-{m:02d}", 100.0 + 10 * m, 100.0) for m in range(1, 13)]
    facts = compute_kpis(make_df(rows))
    dec = get_fact(facts, "Revenue", "2025-12")
    assert dec["trend"] == "up"


def test_trend_flat():
    rows = [("Revenue", f"2025-{m:02d}", 100.0, 100.0) for m in range(1, 13)]
    facts = compute_kpis(make_df(rows))
    dec = get_fact(facts, "Revenue", "2025-12")
    assert dec["trend"] == "flat"


def test_anomaly_flagged_on_spike():
    # Steady ~1% MoM growth, then a 40% crash — should be flagged.
    values = [100.0]
    for _ in range(10):
        values.append(values[-1] * 1.01)
    values.append(values[-1] * 0.60)  # the crash
    rows = [
        ("Revenue", f"2025-{m:02d}", v, 100.0)
        for m, v in zip(range(1, len(values) + 1), values)
    ]
    facts = compute_kpis(make_df(rows), anomaly_z_threshold=2.0)
    crash_period = f"2025-{len(values):02d}"
    crash = get_fact(facts, "Revenue", crash_period)
    assert crash["is_anomaly"] is True or crash["is_anomaly"] == 1
    # earlier steady months are not anomalous
    steady = get_fact(facts, "Revenue", "2025-06")
    assert not steady["is_anomaly"]


def test_handles_24_months_x_10_metrics():
    rows = []
    for k in range(10):
        for y in (2024, 2025):
            for m in range(1, 13):
                rows.append((f"Metric{k}", f"{y}-{m:02d}", 100.0 + m + k, 100.0))
    facts = compute_kpis(make_df(rows))
    assert len(facts) == 240


def test_missing_column_raises():
    df = pd.DataFrame({"metric": ["A"], "period": ["2025-01"], "value": [1.0]})
    with pytest.raises(ValueError, match="budget"):
        compute_kpis(df)
