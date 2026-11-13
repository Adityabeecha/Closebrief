"""Shared monthly fact availability for KPI cards and close summaries."""


DEFAULT_REASON_THRESHOLD = 10.0


def needs_reason(is_anomaly, budget_var_pct, threshold=DEFAULT_REASON_THRESHOLD) -> bool:
    """A KPI needs a written reason when it's an anomaly or misses plan by more than the
    dataset's threshold (percent). Whether a reason already exists is checked separately."""
    if is_anomaly:
        return True
    return budget_var_pct is not None and abs(budget_var_pct) > (threshold or DEFAULT_REASON_THRESHOLD)


def combined_percentage_reason(unit, source_rows=1) -> str | None:
    """A percentage assembled by adding source rows has no usable actual."""
    if unit == "%" and (source_rows or 1) > 1:
        return (f"Combined from {source_rows} rows; percentages can't be added. Import one row per month, "
                "or use a custom KPI to calculate it.")
    return None


def has_usable_value(metric_id, value, unit=None, source_rows=1) -> bool:
    """A matched, non-null actual that is safe to use; zero remains usable."""
    return metric_id is not None and value is not None and combined_percentage_reason(unit, source_rows) is None
