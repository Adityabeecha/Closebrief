"""Shared monthly fact availability for KPI cards and close summaries."""


DEFAULT_REASON_THRESHOLD = 10.0


def _breaches(var_pct, var_abs, threshold, floor) -> bool:
    if var_pct is None or abs(var_pct) <= (threshold or DEFAULT_REASON_THRESHOLD):
        return False
    return not floor or (var_abs is not None and abs(var_abs) >= floor)


def reason_trigger(is_anomaly, budget_var_pct, threshold=DEFAULT_REASON_THRESHOLD, *,
                   budget_var_abs=None, floor=None, parts=()) -> dict | None:
    """Why a KPI needs a written reason, or None. It does when it's an anomaly, when it
    misses plan by more than the dataset's threshold (and, if set, by at least the dollar
    floor), or when any one part of it does (Sales +12.8% inside Opex +3.1%).
    Whether a reason already exists is checked separately."""
    if is_anomaly:
        return {"kind": "anomaly"}
    if _breaches(budget_var_pct, budget_var_abs, threshold, floor):
        return {"kind": "total", "var_pct": budget_var_pct, "var_abs": budget_var_abs}
    worst = None
    for p in parts or ():
        if _breaches(p.get("var_pct"), p.get("var_abs"), threshold, floor) and (
                worst is None or abs(p["var_abs"] or 0) > abs(worst["var_abs"] or 0)):
            worst = p
    if worst:
        return {"kind": "part", "part": worst["member"], "var_pct": worst["var_pct"], "var_abs": worst["var_abs"],
                **({"unplanned": True} if worst.get("unplanned") else {})}
    return None


def needs_reason(is_anomaly, budget_var_pct, threshold=DEFAULT_REASON_THRESHOLD, *,
                 budget_var_abs=None, floor=None, parts=()) -> bool:
    return reason_trigger(is_anomaly, budget_var_pct, threshold, budget_var_abs=budget_var_abs,
                          floor=floor, parts=parts) is not None


def combined_percentage_reason(unit, source_rows=1) -> str | None:
    """A percentage assembled by adding source rows has no usable actual."""
    if unit == "%" and (source_rows or 1) > 1:
        return (f"Combined from {source_rows} rows; percentages can't be added. Import one row per month, "
                "or use a custom KPI to calculate it.")
    return None


def has_usable_value(metric_id, value, unit=None, source_rows=1) -> bool:
    """A matched, non-null actual that is safe to use; zero remains usable."""
    return metric_id is not None and value is not None and combined_percentage_reason(unit, source_rows) is None
