"""Unit tests for the v2.1 multi-metric analysis engine (correlations, trend
streaks, period comparison). Pure/deterministic — no LLM, no HTTP."""

import pytest
from dbharness import use_test_db


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from app.config import settings
    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "corr.db"))
    import app.db as db
    db.init_db()
    c = db.get_connection()

    c.execute("INSERT INTO datasets (id, name, is_active) VALUES (1, 'ds', 1)")
    metrics = {"A": 1, "B": 2, "C": 3, "D": 4}
    for name, mid in metrics.items():
        c.execute(
            "INSERT INTO metrics (id, dataset_id, name, category, unit, direction_good) "
            "VALUES (?, 1, ?, 'Rev', 'USD', 'up')",
            (mid, name),
        )
    periods = ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"]
    series = {
        "A": [10, 20, 30, 40, 50, 60],
        "B": [12, 24, 36, 48, 60, 72],    # ~2x A  -> r ≈ +1
        "C": [60, 50, 40, 30, 20, 10],    # mirror -> r ≈ -1
        "D": [10, 60, 20, 50, 30, 10],    # scattered -> |r| < 0.7 vs A
    }
    for name, vals in series.items():
        for p, v in zip(periods, vals):
            c.execute(
                "INSERT INTO metric_values (metric_id, period, value) VALUES (?, ?, ?)",
                (metrics[name], p, v),
            )
    c.commit()
    yield c
    c.close()


def test_pearson_positive_correlation(conn):
    from app.compute.correlations import compute_correlations
    pairs = compute_correlations(conn, dataset_id=1)
    ab = [p for p in pairs if {p["metric_a"], p["metric_b"]} == {"A", "B"}]
    assert ab, "expected a strong A/B correlation"
    assert ab[0]["r"] > 0.9 and ab[0]["direction"] == "positive"
    assert ab[0]["strength"] == "very_strong"


def test_negative_correlation_detected(conn):
    from app.compute.correlations import compute_correlations
    pairs = compute_correlations(conn, dataset_id=1)
    ac = [p for p in pairs if {p["metric_a"], p["metric_b"]} == {"A", "C"}]
    assert ac and ac[0]["r"] < -0.9 and ac[0]["direction"] == "negative"


def test_no_correlation_below_threshold(conn):
    from app.compute.correlations import compute_correlations
    pairs = compute_correlations(conn, dataset_id=1)
    # Every returned pair clears the 0.7 gate...
    assert all(abs(p["r"]) >= 0.7 for p in pairs)
    # ...and the scattered A/D pair is not reported.
    assert not [p for p in pairs if {p["metric_a"], p["metric_b"]} == {"A", "D"}]


def test_min_overlap_enforced(conn):
    from app.compute.correlations import compute_correlations
    # Requiring 8 shared months (we only have 6) yields nothing.
    assert compute_correlations(conn, dataset_id=1, min_overlap=8) == []


def test_correlations_for_metric_orients_pairs(conn):
    from app.compute.correlations import correlations_for_metric
    out = correlations_for_metric(conn, 1, "C")
    assert out and all(p["metric_a"] == "C" for p in out)


def test_consecutive_trend_detection(conn):
    from app.compute.correlations import detect_consecutive_trends
    # Four consecutive positive MoM months for metric A (id=1).
    for p, mom in [("2025-03", 5.0), ("2025-04", 6.0), ("2025-05", 4.0), ("2025-06", 7.0)]:
        conn.execute(
            "INSERT INTO computed_facts (metric_id, period, value, mom_pct) VALUES (1, ?, 1, ?)",
            (p, mom),
        )
    conn.commit()
    streak = detect_consecutive_trends(conn, 1, "2025-06", min_months=3)
    assert streak is not None
    assert streak["direction"] == "growing"
    assert streak["months"] == 4
    assert streak["start_period"] == "2025-03" and streak["end_period"] == "2025-06"


def test_trend_streak_breaks_on_sign_flip(conn):
    from app.compute.correlations import detect_consecutive_trends
    for p, mom in [("2025-04", 5.0), ("2025-05", -2.0), ("2025-06", 3.0)]:
        conn.execute(
            "INSERT INTO computed_facts (metric_id, period, value, mom_pct) VALUES (2, ?, 1, ?)",
            (p, mom),
        )
    conn.commit()
    # Only one positive month at the end -> below the 3-month minimum.
    assert detect_consecutive_trends(conn, 2, "2025-06", min_months=3) is None


def test_period_comparison(conn):
    from app.compute.correlations import compare_periods
    for p, val, mom in [("2025-05", 50.0, 3.0), ("2025-06", 60.0, 8.0)]:
        conn.execute(
            "INSERT INTO computed_facts (metric_id, period, value, mom_pct) VALUES (1, ?, ?, ?)",
            (p, val, mom),
        )
    conn.commit()
    cmp = compare_periods(conn, 1, "2025-05", "2025-06")
    assert cmp["abs_change"] == 10.0
    assert cmp["pct_change"] == 20.0
    assert cmp["acceleration"] == 5.0        # 8 - 3
    assert cmp["momentum"] == "accelerating"
