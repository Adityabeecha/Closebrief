"""Deterministic, batch-loaded fiscal YTD and one-level contributor views."""

from app.compute.aggregate import months_in, period_key, year_ago_key

NO_BREAKDOWN = "This KPI was imported without a breakdown column."
RATIO_BREAKDOWN = "A percentage can't be split into contributors."


def variance(value, baseline):
    absolute = value - baseline if value is not None and baseline is not None else None
    pct = absolute / abs(baseline) * 100 if absolute is not None and baseline else None
    return absolute, pct


def load_values(conn, dataset_id):
    result = {}
    for row in conn.execute("""SELECT mv.* FROM metric_values mv JOIN metrics m ON m.id=mv.metric_id
                               WHERE m.dataset_id=? ORDER BY mv.period""", (dataset_id,)).fetchall():
        result.setdefault(row["metric_id"], []).append(dict(row))
    return result


def breakdown_coverage(conn, dataset_id, covered):
    placeholders = ",".join("?" for _ in covered)
    result = {}
    for row in conn.execute(f"""SELECT DISTINCT mb.metric_id, mb.period, mb.dimension FROM metric_breakdowns mb
        JOIN metrics m ON m.id=mb.metric_id WHERE m.dataset_id=? AND mb.period IN ({placeholders})""",
                            (dataset_id, *covered)).fetchall():
        result.setdefault(row["metric_id"], []).append(dict(row))
    return result


def part_variances(conn, dataset_id, months=None) -> dict:
    """{(metric_id, month): [{member, var_abs, var_pct}]} for parts with a plan."""
    sql = """SELECT mb.metric_id, mb.period, mb.member, mb.value, mb.budget FROM metric_breakdowns mb
             JOIN metrics m ON m.id = mb.metric_id WHERE m.dataset_id = ? AND mb.budget IS NOT NULL"""
    args = [dataset_id]
    if months:
        sql += f" AND mb.period IN ({','.join('?' for _ in months)})"
        args += list(months)
    out = {}
    for r in conn.execute(sql, tuple(args)).fetchall():
        if r["value"] is None:
            continue
        absolute, pct = variance(r["value"], r["budget"])
        out.setdefault((r["metric_id"], r["period"]), []).append(
            {"member": r["member"], "var_abs": absolute, "var_pct": pct})
    return out


def breakdown_available(actuals, breakdowns, covered, agg_type):
    expected = {r["period"] for r in actuals if r["period"] in covered}
    if agg_type == "balance" and expected:
        expected = {max(expected)}
    included = [r for r in breakdowns if r["period"] in expected]
    return bool(expected) and {r["period"] for r in included} == expected and len({r["dimension"] for r in included}) == 1


def ytd(rows, period, fiscal_start, agg_type, unit):
    if agg_type == "ratio" or unit == "%":
        return None
    fiscal_year = period_key(period, "year", fiscal_start)
    covered = [p for p in months_in(fiscal_year, "year", fiscal_start) if p <= period]
    by_period = {r["period"]: r for r in rows}
    items = [by_period[p] for p in covered if p in by_period]
    if not items:
        return None
    budgets = [r["budget"] for r in items if r["budget"] is not None]
    forecasts = [r["forecast"] for r in items if r["forecast"] is not None]
    if agg_type == "balance":
        current = by_period.get(period, {})
        value, budget, forecast = current.get("value"), current.get("budget"), current.get("forecast")
        prior = by_period.get(year_ago_key(period, "month"), {}).get("value")
    else:
        value = sum(r["value"] for r in items if r["value"] is not None)
        budget = sum(budgets) if len(budgets) == len(covered) else None
        # Like the budget: a year-to-date forecast only when every month so far has one.
        forecast = sum(forecasts) if len(forecasts) == len(covered) else None
        prior_items = [by_period.get(year_ago_key(p, "month"), {}).get("value") for p in covered]
        prior = sum(prior_items) if all(v is not None for v in prior_items) else None
    absolute, pct = variance(value, budget)
    return {"months": len(covered), "value": value, "budget": budget, "forecast": forecast,
            "budget_months": len(budgets), "var_abs": absolute, "var_pct": pct,
            "prior_value": prior, "yoy_pct": variance(value, prior)[1]}


def contributors(conn, metric, period, covered, agg_type, unit):
    output = {"metric": metric["name"], "period": period, "dimension": None, "unit": unit,
              "aggregation_type": agg_type, "members": [],
              "total": {"value": None, "budget": None, "var_abs": None}, "available": False, "reason": NO_BREAKDOWN}
    if agg_type == "ratio" or unit == "%":
        output["reason"] = RATIO_BREAKDOWN
        return output
    marks = ",".join("?" for _ in covered)
    actuals = [dict(r) for r in conn.execute(f"SELECT * FROM metric_values WHERE metric_id=? AND period IN ({marks})",
                                            (metric["id"], *covered)).fetchall()]
    if agg_type == "balance" and actuals:
        covered = [max(r["period"] for r in actuals)]
        actuals = [r for r in actuals if r["period"] in covered]
    marks = ",".join("?" for _ in covered)
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM metric_breakdowns WHERE metric_id=? AND period IN ({marks})", (metric["id"], *covered)).fetchall()]
    if not breakdown_available(actuals, rows, covered, agg_type):
        return output
    grouped = {}
    for row in rows:
        grouped.setdefault(row["member"], []).append(row)
    def total(items, field):
        values = [r[field] for r in items if r[field] is not None]
        return sum(values) if values else None
    value, budget = total(actuals, "value"), total(actuals, "budget")
    total_variance = variance(value, budget)[0]
    members = []
    for member, items in grouped.items():
        v, b, f = (total(items, field) for field in ("value", "budget", "forecast"))
        absolute, pct = variance(v, b)
        members.append({"member": member, "value": v, "budget": b, "forecast": f, "var_abs": absolute,
                        "var_pct": pct, "share_of_variance": absolute / total_variance if absolute is not None and total_variance else None})
    has_budgets = any(r["budget"] is not None for r in members)
    members.sort(key=lambda r: (-abs((r["var_abs"] if has_budgets else r["value"]) or 0), r["member"]))
    return {**output, "dimension": rows[0]["dimension"], "members": members,
            "total": {"value": value, "budget": budget, "var_abs": total_variance}, "available": True, "reason": None}
