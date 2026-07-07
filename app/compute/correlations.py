"""Multi-metric analysis (v2.1). Deterministic, LLM-free: correlations between
metric pairs, consecutive-direction trend streaks, and period-over-period
comparison (deltas of deltas). Results enrich the narrative prompt as facts —
the model still never computes numbers itself.

All functions take a DB connection using the app's `?`-style placeholders
(works for both SQLite and the Postgres wrapper).
"""

from __future__ import annotations

import numpy as np

CORR_THRESHOLD = 0.7          # |r| below this is not reported
MIN_OVERLAP = 6               # months two metrics must share to be comparable


def _month_key(period: str) -> tuple[int, int]:
    y, m = period.split("-")[:2]
    return int(y), int(m)


def _months_between(a: str, b: str) -> int:
    (ay, am), (by, bm) = _month_key(a), _month_key(b)
    return (by * 12 + bm) - (ay * 12 + am)


def _series_by_metric(conn, dataset_id: int, upto_period: str | None) -> dict[int, dict]:
    """Return {metric_id: {"name": str, "values": {period: value}}} for the
    dataset, optionally only periods <= upto_period."""
    rows = conn.execute(
        """
        SELECT m.id AS metric_id, m.name AS name, mv.period AS period, mv.value AS value
        FROM metrics m
        JOIN metric_values mv ON mv.metric_id = m.id
        WHERE m.dataset_id = ?
        ORDER BY m.id, mv.period
        """,
        (dataset_id,),
    ).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        if upto_period is not None and _months_between(r["period"], upto_period) < 0:
            continue
        entry = out.setdefault(r["metric_id"], {"name": r["name"], "values": {}})
        entry["values"][r["period"]] = float(r["value"])
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    x, y = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    if x.std() == 0 or y.std() == 0:
        return None
    r = float(np.corrcoef(x, y)[0, 1])
    if np.isnan(r):
        return None
    return r


def compute_correlations(
    conn,
    dataset_id: int,
    period: str | None = None,
    *,
    threshold: float = CORR_THRESHOLD,
    min_overlap: int = MIN_OVERLAP,
) -> list[dict]:
    """Every metric pair with >= `min_overlap` overlapping months and
    |Pearson r| >= `threshold`, strongest first."""
    series = _series_by_metric(conn, dataset_id, period)
    ids = sorted(series)
    pairs: list[dict] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = series[ids[i]], series[ids[j]]
            shared = sorted(set(a["values"]) & set(b["values"]))
            if len(shared) < min_overlap:
                continue
            r = _pearson([a["values"][p] for p in shared], [b["values"][p] for p in shared])
            if r is None or abs(r) < threshold:
                continue
            pairs.append({
                "metric_a": a["name"],
                "metric_b": b["name"],
                "r": round(r, 3),
                "months": len(shared),
                "direction": "positive" if r > 0 else "negative",
                "strength": "very_strong" if abs(r) >= 0.9 else "strong",
            })
    pairs.sort(key=lambda p: abs(p["r"]), reverse=True)
    return pairs


def correlations_for_metric(conn, dataset_id: int, metric: str, period: str | None = None) -> list[dict]:
    """Correlated pairs that involve `metric`, phrased so `metric` is metric_a."""
    out = []
    for p in compute_correlations(conn, dataset_id, period):
        if p["metric_a"] == metric:
            out.append(p)
        elif p["metric_b"] == metric:
            out.append({**p, "metric_a": metric, "metric_b": p["metric_a"]})
    return out


def detect_consecutive_trends(conn, metric_id: int, period: str, min_months: int = 3) -> dict | None:
    """Walk backwards from `period` over computed_facts.mom_pct; if the sign of
    the month-over-month change is the same for >= `min_months` consecutive
    months, return a TrendStreak-shaped dict, else None."""
    rows = conn.execute(
        """
        SELECT period, mom_pct FROM computed_facts
        WHERE metric_id = ? AND mom_pct IS NOT NULL
        ORDER BY period
        """,
        (metric_id,),
    ).fetchall()
    # Keep months at or before the target period (months_between(row, target) >= 0).
    hist = [(r["period"], float(r["mom_pct"])) for r in rows if _months_between(r["period"], period) >= 0]
    if len(hist) < min_months:
        return None

    def sign(v: float) -> int:
        return 1 if v > 0 else -1 if v < 0 else 0

    last_sign = sign(hist[-1][1])
    if last_sign == 0:
        return None
    streak = []
    for p, v in reversed(hist):
        if sign(v) == last_sign:
            streak.append(p)
        else:
            break
    if len(streak) < min_months:
        return None
    streak.reverse()
    name_row = conn.execute("SELECT name FROM metrics WHERE id = ?", (metric_id,)).fetchone()
    return {
        "metric": name_row["name"] if name_row else str(metric_id),
        "direction": "growing" if last_sign > 0 else "declining",
        "months": len(streak),
        "start_period": streak[0],
        "end_period": streak[-1],
    }


def compare_periods(conn, metric_id: int, period_a: str, period_b: str) -> dict | None:
    """Side-by-side comparison of two periods for one metric, including the
    delta-of-deltas (whether the movement accelerated or decelerated).
    `period_a` is the reference (earlier) period, `period_b` the current one."""
    def load(p):
        return conn.execute(
            """SELECT value, mom_pct, budget_var_pct FROM computed_facts
               WHERE metric_id = ? AND period = ?""",
            (metric_id, p),
        ).fetchone()

    ra, rb = load(period_a), load(period_b)
    if ra is None or rb is None:
        return None
    va, vb = float(ra["value"]), float(rb["value"])
    abs_change = vb - va
    pct_change = (abs_change / abs(va) * 100.0) if va else None
    mom_a = None if ra["mom_pct"] is None else float(ra["mom_pct"])
    mom_b = None if rb["mom_pct"] is None else float(rb["mom_pct"])
    accel = None if (mom_a is None or mom_b is None) else round(mom_b - mom_a, 2)
    momentum = None
    if accel is not None:
        momentum = "accelerating" if accel > 0 else "decelerating" if accel < 0 else "steady"
    name_row = conn.execute("SELECT name FROM metrics WHERE id = ?", (metric_id,)).fetchone()
    return {
        "metric": name_row["name"] if name_row else str(metric_id),
        "period_a": period_a,
        "period_b": period_b,
        "value_a": round(va, 4),
        "value_b": round(vb, 4),
        "abs_change": round(abs_change, 4),
        "pct_change": None if pct_change is None else round(pct_change, 2),
        "mom_pct_a": mom_a,
        "mom_pct_b": mom_b,
        "acceleration": accel,
        "momentum": momentum,
    }
