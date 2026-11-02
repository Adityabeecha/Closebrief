"""Shared monthly fact availability for KPI cards and close summaries."""


def has_usable_value(metric_id, value) -> bool:
    """A matched metric with a non-null actual; zero is a usable value."""
    return metric_id is not None and value is not None
