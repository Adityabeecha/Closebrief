"""Deterministic KPI math. Pure pandas/numpy — no LLM calls, no I/O side effects.

This module is the enforcement point for the PRD's non-negotiable rule:
the LLM never computes numbers. Everything here must be unit-testable
without a network connection or an API key.
"""

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
                    "trend": trend,
                    "is_anomaly": False,  # filled in below once mom_series is complete
                }
            )

        anomalies = _flag_anomalies(mom_series, anomaly_z_threshold)
        for i in range(len(anomalies)):
            results[len(results) - len(anomalies) + i]["is_anomaly"] = anomalies[i]

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
    if dataset_id is not None:
        rows = conn.execute(
            """
            SELECT m.id AS metric_id, m.name AS metric, mv.period, mv.value, mv.budget
            FROM metric_values mv JOIN metrics m ON m.id = mv.metric_id
            WHERE m.dataset_id = ?
            """,
            (dataset_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT m.id AS metric_id, m.name AS metric, mv.period, mv.value, mv.budget
            FROM metric_values mv JOIN metrics m ON m.id = mv.metric_id
            """
        ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df

    facts = compute_kpis(df.drop(columns=["metric_id"]), anomaly_z_threshold)
    metric_ids = {r["metric"]: r["metric_id"] for r in df.to_dict("records")}

    cur = conn.cursor()
    for _, f in facts.iterrows():
        cur.execute(
            """
            INSERT INTO computed_facts
                (metric_id, period, value, prior_value, mom_pct, yoy_pct,
                 budget_var_abs, budget_var_pct, trend, is_anomaly)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metric_id, period) DO UPDATE SET
                value=excluded.value, prior_value=excluded.prior_value,
                mom_pct=excluded.mom_pct, yoy_pct=excluded.yoy_pct,
                budget_var_abs=excluded.budget_var_abs, budget_var_pct=excluded.budget_var_pct,
                trend=excluded.trend, is_anomaly=excluded.is_anomaly
            """,
            (
                metric_ids[f["metric"]],
                f["period"],
                f["value"],
                f["prior_value"],
                f["mom_pct"],
                f["yoy_pct"],
                f["budget_var_abs"],
                f["budget_var_pct"],
                f["trend"],
                int(bool(f["is_anomaly"])),
            ),
        )
    conn.commit()
    return facts
