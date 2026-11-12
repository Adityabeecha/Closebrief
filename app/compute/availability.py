"""Shared monthly fact availability for KPI cards and close summaries."""


def combined_percentage_reason(unit, source_rows=1) -> str | None:
    """A percentage assembled by adding source rows has no usable actual."""
    if unit == "%" and (source_rows or 1) > 1:
        return (f"Combined from {source_rows} rows; percentages can't be added. Import one row per month, "
                "or use a custom KPI to calculate it.")
    return None


def has_usable_value(metric_id, value, unit=None, source_rows=1) -> bool:
    """A matched, non-null actual that is safe to use; zero remains usable."""
    return metric_id is not None and value is not None and combined_percentage_reason(unit, source_rows) is None
