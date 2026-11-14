"""Amounts reported in thousands or millions ("$ in thousands" in a 10-Q).

The app stores real amounts: a currency KPI imported from a file "in thousands" is
multiplied by 1,000, so the screens, the drafted commentary and the figure check all
read "$947.6M", not "$948K". Percentages, counts and days are never scaled. Each
metric remembers the factor applied (metrics.value_scale), so changing a KPI's unit
later (Customers >$1M from Amount to Count) undoes or applies the scaling."""

from __future__ import annotations

import pandas as pd

SCALES = (1, 1000, 1_000_000)
NON_CURRENCY_UNITS = {"%", "percent", "ratio", "x", "count", "number", "days", ""}
_SCALED_COLS = ("value", "budget", "forecast", "price", "budget_price")


def is_currency(unit: str | None) -> bool:
    return str(unit or "USD").strip().lower() not in NON_CURRENCY_UNITS


def metric_units(conn, dataset_id: int | None, metrics) -> dict[str, str]:
    """The unit each metric will have: its saved KPI config, else the library's guess."""
    from app.kpis.library import suggest_import_defaults

    saved = {}
    if dataset_id is not None:
        saved = {r["source_metric"]: r["unit"] for r in conn.execute(
            "SELECT source_metric, unit FROM kpi_configs WHERE dataset_id = ?", (dataset_id,)).fetchall()}
    return {m: saved.get(m) or suggest_import_defaults(str(m)).get("unit") or "USD" for m in metrics}


def scale_frame(canonical: pd.DataFrame, scale: int, units: dict[str, str]) -> set[str]:
    """Multiply currency metrics' amounts in place; returns the metrics scaled."""
    if not scale or scale == 1 or canonical.empty:
        return set()
    scaled = {m for m, u in units.items() if is_currency(u)}
    mask = canonical["metric"].isin(scaled)
    for col in _SCALED_COLS:
        if col in canonical.columns:
            canonical.loc[mask, col] = pd.to_numeric(canonical.loc[mask, col], errors="coerce") * scale
    return scaled


def mark_scaled(conn, dataset_id: int, metrics: set[str], scale: int) -> None:
    if scale and scale != 1:
        conn.execute("UPDATE datasets SET amount_scale = ? WHERE id = ?", (scale, dataset_id))
    for m in metrics:
        conn.execute("UPDATE metrics SET value_scale = ? WHERE dataset_id = ? AND name = ?", (scale, dataset_id, m))


def rescale_metric(conn, metric_id: int, factor: float) -> None:
    """Multiply every stored amount of one metric (values, breakdown members)."""
    if factor == 1:
        return
    conn.execute(
        """UPDATE metric_values SET value = value * ?, budget = budget * ?, forecast = forecast * ?,
           price = price * ?, budget_price = budget_price * ? WHERE metric_id = ?""",
        (factor, factor, factor, factor, factor, metric_id))
    conn.execute("UPDATE metric_breakdowns SET value = value * ?, budget = budget * ?, forecast = forecast * ? WHERE metric_id = ?",
                 (factor, factor, factor, metric_id))


def align_metric_scale(conn, dataset_id: int, source_metric: str, unit: str | None) -> bool:
    """After a unit change: a currency KPI carries the dataset's scale, anything else 1.
    Returns True when stored amounts changed (facts need recomputing)."""
    row = conn.execute(
        """SELECT m.id, COALESCE(m.value_scale, 1) AS cur, COALESCE(d.amount_scale, 1) AS ds
           FROM metrics m JOIN datasets d ON d.id = m.dataset_id WHERE m.dataset_id = ? AND m.name = ?""",
        (dataset_id, source_metric)).fetchone()
    if row is None:
        return False
    want = row["ds"] if is_currency(unit) else 1
    if float(want) == float(row["cur"]):
        return False
    rescale_metric(conn, row["id"], float(want) / float(row["cur"]))
    conn.execute("UPDATE metrics SET value_scale = ? WHERE id = ?", (want, row["id"]))
    return True
