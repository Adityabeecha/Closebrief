"""Anomaly root-cause decomposition (v5.3).

When a KPI moves — especially when it trips the anomaly guard — attribute the
swing *deterministically*: how far it is from its own recent baseline (z-score),
how much is price vs volume vs mix, which correlated metrics moved with it, and
whether it's a one-off or a sustained streak. The LLM only phrases this; every
number here is computed, so the "why" is grounded and never invented.
"""

from __future__ import annotations

import statistics


def _trailing_values(conn, metric_id: int, period: str, window: int = 12) -> list[float]:
    rows = conn.execute(
        """SELECT value FROM metric_values
           WHERE metric_id = ? AND period < ? ORDER BY period DESC LIMIT ?""",
        (metric_id, period, window),
    ).fetchall()
    return [float(r["value"]) for r in rows if r["value"] is not None]


def _pvm(conn, metric_id: int, period: str) -> list[dict] | None:
    """Price/Volume/Mix attribution for the period, from the stored bridge or an
    on-the-fly one when only simple qty/price detail exists. None when neither."""
    comps = None
    row = conn.execute(
        "SELECT volume, price, mix FROM pvm_bridges WHERE metric_id = ? AND period = ?",
        (metric_id, period),
    ).fetchone()
    if row is not None:
        comps = [("Volume", row["volume"]), ("Price", row["price"]), ("Mix", row["mix"])]
    else:
        mv = conn.execute(
            """SELECT quantity, price, budget_quantity, budget_price
               FROM metric_values WHERE metric_id = ? AND period = ?""",
            (metric_id, period),
        ).fetchone()
        if mv is not None:
            from app.compute.pvm import bridge_for_metric_row
            vb = bridge_for_metric_row(
                mv["quantity"], mv["budget_quantity"], mv["price"], mv["budget_price"])
            if vb is not None:
                comps = [("Volume", vb.volume), ("Price", vb.price), ("Mix", vb.mix)]
    if not comps:
        return None
    total = sum(abs(v) for _, v in comps) or 1.0
    out = [{"component": n, "impact": round(float(v), 2),
            "share_pct": round(100 * abs(v) / total, 1)} for n, v in comps]
    out.sort(key=lambda c: -abs(c["impact"]))
    return out


def decompose(conn, dataset_id: int, metric: str, period: str) -> dict | None:
    """Deterministic root-cause breakdown for `metric` in `period`, or None when
    the metric/period has no computed fact."""
    from app.compute.correlations import correlations_for_metric, detect_consecutive_trends

    mrow = conn.execute(
        "SELECT id, unit FROM metrics WHERE dataset_id = ? AND name = ?", (dataset_id, metric)
    ).fetchone()
    if mrow is None:
        return None
    metric_id, unit = mrow["id"], mrow["unit"]
    cf = conn.execute(
        """SELECT value, prior_value, mom_pct, yoy_pct, budget_var_abs, budget_var_pct, is_anomaly
           FROM computed_facts WHERE metric_id = ? AND period = ?""",
        (metric_id, period),
    ).fetchone()
    if cf is None or cf["value"] is None:
        return None
    value = float(cf["value"])

    # How far from its own recent baseline (trailing window, current excluded).
    hist = _trailing_values(conn, metric_id, period)
    z = mean = std = None
    if len(hist) >= 3:
        mean = round(statistics.fmean(hist), 2)
        std = statistics.pstdev(hist)
        z = round((value - mean) / std, 2) if std > 0 else None
        std = round(std, 2)

    pvm = _pvm(conn, metric_id, period)

    # Correlated metrics that moved with this one (top by |r|), as candidate drivers.
    drivers = [
        {"metric": p["metric_b"], "r": p["r"], "direction": p["direction"],
         "strength": p.get("strength")}
        for p in correlations_for_metric(conn, dataset_id, metric, period)
    ]
    drivers.sort(key=lambda d: -abs(d["r"]))
    drivers = drivers[:3]

    streak = detect_consecutive_trends(conn, metric_id, period)
    trend = None
    if streak:
        trend = {"direction": streak.get("direction"), "months": streak.get("months")}

    # Pick the single biggest explainer so the UI can lead with it.
    primary = _primary_factor(cf, pvm, drivers, unit)

    return {
        "metric": metric, "period": period, "unit": unit,
        "value": round(value, 2),
        "prior_value": round(float(cf["prior_value"]), 2) if cf["prior_value"] is not None else None,
        "mom_pct": cf["mom_pct"], "yoy_pct": cf["yoy_pct"],
        "budget_var_abs": cf["budget_var_abs"], "budget_var_pct": cf["budget_var_pct"],
        "is_anomaly": bool(cf["is_anomaly"]),
        "z_score": z, "baseline_mean": mean, "baseline_std": std,
        "pvm": pvm, "drivers": drivers, "trend": trend,
        "primary_factor": primary,
    }


def _primary_factor(cf, pvm, drivers, unit) -> str:
    """A one-line, deterministic 'biggest reason' for the UI headline."""
    if pvm:
        top = pvm[0]
        return f"{top['component']} — {top['share_pct']:g}% of the variance"
    if cf["budget_var_pct"] is not None and abs(cf["budget_var_pct"]) >= 5:
        return f"{cf['budget_var_pct']:+.1f}% vs plan"
    if drivers:
        d = drivers[0]
        return f"moves with {d['metric']} (r={d['r']})"
    if cf["mom_pct"] is not None:
        return f"{cf['mom_pct']:+.1f}% month-over-month"
    return "no dominant single driver"
