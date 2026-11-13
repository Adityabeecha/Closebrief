"""Deterministic KPI math. Pure pandas/numpy — no LLM calls, no I/O side effects.

This module is the enforcement point for the PRD's non-negotiable rule:
the LLM never computes numbers. Everything here must be unit-testable
without a network connection or an API key.
"""

import math
import sqlite3

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["metric", "period", "value", "budget"]


def _period_to_month(period: str) -> pd.Period:
    return pd.Period(period, freq="M")


def compute_kpis(df: pd.DataFrame, anomaly_z_threshold: float = 2.0) -> pd.DataFrame:
    """Given rows of (metric, period, value, budget), return one row per
    metric+period with value, prior_value, mom_pct, yoy_pct, budget variance,
    trend, and an anomaly flag. `period` must be "YYYY-MM" strings.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"compute_kpis: missing column(s): {', '.join(missing)}")

    work = df.copy()
    work["_month"] = work["period"].map(_period_to_month)

    results: list[dict] = []
    for metric, g in work.groupby("metric"):
        g = g.sort_values("_month").reset_index(drop=True)
        value_by_month = dict(zip(g["_month"], g["value"]))

        mom_series: list[float | None] = []
        for i, row in g.iterrows():
            month = row["_month"]
            value = row["value"]
            budget = row["budget"]

            prior_month_value = value_by_month.get(month - 1)
            prior_year_value = value_by_month.get(month - 12)

            mom_pct = (
                None
                if prior_month_value is None or prior_month_value == 0
                else (value - prior_month_value) / abs(prior_month_value) * 100.0
            )
            yoy_pct = (
                None
                if prior_year_value is None or prior_year_value == 0
                else (value - prior_year_value) / abs(prior_year_value) * 100.0
            )

            if pd.isna(budget) or budget is None:
                budget_var_abs = None
                budget_var_pct = None
            else:
                budget_var_abs = value - budget
                budget_var_pct = (
                    None if budget == 0 else (value - budget) / abs(budget) * 100.0
                )

            forecast = row.get("forecast")
            forecast_var_abs = None if forecast is None or pd.isna(forecast) else value - forecast
            forecast_var_pct = (None if forecast_var_abs is None or forecast == 0
                                else forecast_var_abs / abs(forecast) * 100.0)
            mom_series.append(mom_pct)

            # Trend: linear-regression slope over the trailing 12 months
            # (including the current one), sign gives direction.
            trailing_months = [month - k for k in range(11, -1, -1)]
            trailing_values = [value_by_month[m] for m in trailing_months if m in value_by_month]
            trend = _trend_direction(trailing_values)

            results.append(
                {
                    "metric": metric,
                    "period": str(month),
                    "value": float(value),
                    "prior_value": None if prior_month_value is None else float(prior_month_value),
                    "mom_pct": mom_pct,
                    "yoy_pct": yoy_pct,
                    "budget_var_abs": budget_var_abs,
                    "budget_var_pct": budget_var_pct,
                    "forecast_var_abs": forecast_var_abs,
                    "forecast_var_pct": forecast_var_pct,
                    "trend": trend,
                    "is_anomaly": False,  # filled in below once mom_series is complete
                    "anomaly_basis": None,
                    "anomaly_z": None,
                }
            )

        judged = judge_anomalies(list(g["_month"]), mom_series, anomaly_z_threshold)
        start = len(results) - len(judged)
        for i, (flag, basis, z) in enumerate(judged):
            results[start + i].update(is_anomaly=flag, anomaly_basis=basis, anomaly_z=z)

    return pd.DataFrame(results)


def _trend_direction(values: list[float], flat_slope_threshold: float = 0.005) -> str | None:
    """'up' / 'down' / 'flat' from the sign of a linear-regression slope,
    normalized by the series mean so the threshold is scale-independent.
    Returns None when there isn't enough history to judge a trend.
    """
    if len(values) < 2:
        return None
    x = np.arange(len(values))
    y = np.array(values, dtype=float)
    slope, _ = np.polyfit(x, y, 1)
    mean_abs = np.mean(np.abs(y)) or 1.0
    normalized_slope = slope / mean_abs
    if normalized_slope > flat_slope_threshold:
        return "up"
    if normalized_slope < -flat_slope_threshold:
        return "down"
    return "flat"


def _z(value: float, history: list[float], min_std: float = 0.0) -> float:
    std = max(float(np.std(history)), min_std)
    return 0.0 if std == 0 else (value - float(np.mean(history))) / std


# A seasonal pattern can repeat exactly, leaving no spread in the year-on-year
# differences; judge a departure from it against at least 1 percentage point.
_SEASONAL_MIN_STD = 1.0


def judge_anomalies(months: list[int], mom_series: list[float | None],
                    z_threshold: float) -> list[tuple[bool, str | None, float | None]]:
    """(is_anomaly, basis, z) per month, in order.

    'seasonal': when the same month a year earlier has its own month-on-month change,
    judge d = this change minus last year's change for that month against the trailing
    history of such differences, so a December that rises every year isn't flagged
    every year. Needs 3 earlier differences; otherwise fall back to
    'month_on_month', today's rule (this change against the trailing changes, 3 needed).
    None when there isn't enough history to judge."""
    mom_by_month = {m: v for m, v in zip(months, mom_series) if v is not None}
    out: list[tuple[bool, str | None, float | None]] = []
    moms: list[float] = []
    diffs: list[float] = []
    for month, mom in zip(months, mom_series):
        last_year = mom_by_month.get(month - 12)
        diff = None if mom is None or last_year is None else mom - last_year
        if diff is not None and len(diffs) >= 3:
            z = _z(diff, diffs, _SEASONAL_MIN_STD)
            out.append((abs(z) > z_threshold, "seasonal", round(z, 2)))
        elif mom is not None and len(moms) >= 3:
            z = _z(mom, moms)
            out.append((abs(z) > z_threshold, "month_on_month", round(z, 2)))
        else:
            out.append((False, None, None))
        if mom is not None:
            moms.append(mom)
        if diff is not None:
            diffs.append(diff)
    return out


def _flag_anomalies(mom_series: list[float | None], z_threshold: float) -> list[bool]:
    """Flag a MoM change as anomalous when its z-score vs the trailing
    history of MoM changes (up to that point) exceeds z_threshold.
    Needs at least 3 prior MoM values to compute a meaningful std dev.
    """
    flags: list[bool] = []
    history: list[float] = []
    for mom in mom_series:
        if mom is None or len(history) < 3:
            flags.append(False)
        else:
            std = float(np.std(history))
            mean = float(np.mean(history))
            z = 0.0 if std == 0 else (mom - mean) / std
            flags.append(abs(z) > z_threshold)
        if mom is not None:
            history.append(mom)
    return flags


def compute_and_store(
    conn: sqlite3.Connection, dataset_id: int | None = None, anomaly_z_threshold: float = 2.0
) -> pd.DataFrame:
    """Compute facts for a dataset's metric_values and upsert into computed_facts.
    Scoped to dataset_id so it never mixes metrics from other uploads (and so a
    shared metric name across datasets doesn't collide in the name->id map)."""
    from app.close_control import serialize_writes

    if dataset_id is None and getattr(conn, "backend", "sqlite") == "postgres":
        for row in conn.execute("SELECT id FROM datasets ORDER BY id").fetchall():
            serialize_writes(conn, row["id"])
    else:
        serialize_writes(conn, dataset_id)
    if dataset_id is not None:
        rows = conn.execute(
            """
            SELECT m.id AS metric_id, m.name AS metric, mv.period, mv.value, mv.budget, mv.forecast
            FROM metric_values mv JOIN metrics m ON m.id = mv.metric_id
            WHERE m.dataset_id = ?
            """,
            (dataset_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT m.id AS metric_id, m.name AS metric, mv.period, mv.value, mv.budget, mv.forecast
            FROM metric_values mv JOIN metrics m ON m.id = mv.metric_id
            """
        ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df

    groups = []
    for metric_id, group in df.groupby("metric_id"):
        computed = compute_kpis(group.drop(columns=["metric_id"]), anomaly_z_threshold)
        computed["metric_id"] = metric_id
        groups.append(computed)
    facts = pd.concat(groups, ignore_index=True)

    # Facts as they stood before this recompute, to spot figures that change.
    metric_ids = sorted({int(m) for m in facts["metric_id"]})
    before = {}
    if metric_ids:
        marks = ",".join("?" * len(metric_ids))
        for r in conn.execute(
            f"""SELECT metric_id, period, value, prior_value, mom_pct, yoy_pct,
                       budget_var_abs, budget_var_pct, forecast_var_abs, forecast_var_pct
                FROM computed_facts WHERE metric_id IN ({marks})""",
            tuple(metric_ids),
        ).fetchall():
            before[(int(r["metric_id"]), r["period"])] = tuple(r[k] for k in _COMPARED)

    # One batch: on a remote database each statement is a round trip.
    sql = """
        INSERT INTO computed_facts
            (metric_id, period, value, prior_value, mom_pct, yoy_pct,
             budget_var_abs, budget_var_pct, forecast_var_abs, forecast_var_pct, trend, is_anomaly,
             anomaly_basis, anomaly_z)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(metric_id, period) DO UPDATE SET
            value=excluded.value, prior_value=excluded.prior_value,
            mom_pct=excluded.mom_pct, yoy_pct=excluded.yoy_pct,
            budget_var_abs=excluded.budget_var_abs, budget_var_pct=excluded.budget_var_pct,
            forecast_var_abs=excluded.forecast_var_abs, forecast_var_pct=excluded.forecast_var_pct,
            trend=excluded.trend, is_anomaly=excluded.is_anomaly,
            anomaly_basis=excluded.anomaly_basis, anomaly_z=excluded.anomaly_z
        """
    rows = [
        (
            int(f["metric_id"]),
            f["period"],
            _db_num(f["value"]),
            _db_num(f["prior_value"]),
            _db_num(f["mom_pct"]),
            _db_num(f["yoy_pct"]),
            _db_num(f["budget_var_abs"]),
            _db_num(f["budget_var_pct"]),
            _db_num(f["forecast_var_abs"]),
            _db_num(f["forecast_var_pct"]),
            f["trend"] if isinstance(f["trend"], str) else None,
            int(bool(f["is_anomaly"])),
            f["anomaly_basis"] if isinstance(f["anomaly_basis"], str) else None,
            _db_num(f["anomaly_z"]),
        )
        for _, f in facts.iterrows()
    ]
    conn.executemany(sql, rows)
    changed = [
        key for key in ((int(f["metric_id"]), f["period"]) for _, f in facts.iterrows())
        if key in before and _figures_differ(before[key], tuple(facts_row(facts, key)))
    ]
    reset_changed_commentary(conn, changed)
    conn.commit()
    return facts.drop(columns=["metric_id"])


def refresh_stale_anomalies(conn) -> int:
    """Recompute datasets whose facts predate the seasonal anomaly rule.

    Under the current rule a flagged month always records how it was judged, so a
    flagged month with no basis can only come from the old rule. Recomputing leaves
    the figures (and so approvals) as they are; afterwards no row matches, so this
    runs once per dataset. Returns how many datasets were recomputed."""
    stale = [r["dataset_id"] for r in conn.execute(
        """SELECT DISTINCT m.dataset_id FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
           WHERE cf.is_anomaly = 1 AND cf.anomaly_basis IS NULL""").fetchall()]
    for ds in stale:
        compute_and_store(conn, ds)
    return len(stale)


def _db_num(v):
    """A missing pandas number is NaN. SQLite stores NaN as NULL but Postgres keeps it
    as the float NaN, so write None for anything missing."""
    return None if v is None or pd.isna(v) else float(v)


_COMPARED = ("value", "prior_value", "mom_pct", "yoy_pct", "budget_var_abs", "budget_var_pct", "forecast_var_abs", "forecast_var_pct")


def facts_row(facts: pd.DataFrame, key: tuple[int, str]) -> list:
    row = facts[(facts["metric_id"] == key[0]) & (facts["period"] == key[1])].iloc[0]
    return [None if pd.isna(row[k]) else float(row[k]) for k in _COMPARED]


def _figures_differ(old: tuple, new: tuple) -> bool:
    for a, b in zip(old, new):
        if (a is None) != (b is None):
            return True
        if a is not None and not math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9):
            return True
    return False


def reset_changed_commentary(conn, changed: list[tuple[int, str]]) -> None:
    """A re-import changed these figures, so commentary written about the old
    numbers can't stay approved or count as checked. Approval is cleared (the
    KPI goes back to review) and the figure check is marked as not run."""
    for metric_id, period in changed:
        conn.execute(
            """UPDATE generated_reports
               SET review_status = CASE WHEN review_status = 'approved' THEN NULL ELSE review_status END,
                   faithfulness = 'unchecked'
               WHERE metric_id = ? AND period = ?
                 AND NOT EXISTS (SELECT 1 FROM close_locks cl JOIN metrics m ON m.dataset_id=cl.dataset_id
                                 WHERE m.id=generated_reports.metric_id AND cl.period=generated_reports.period)""",
            (metric_id, period),
        )
