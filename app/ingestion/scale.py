"""Amounts reported in thousands or millions ("$ in thousands" in a 10-Q).

The app stores real amounts: a currency KPI imported from a file "in thousands" is
multiplied by 1,000, so the screens, the drafted commentary and the figure check all
read "$947.6M", not "$948K". Percentages, counts and days are never scaled. Each
metric remembers the factor applied (metrics.value_scale), so changing a KPI's unit
later (Customers >$1M from Amount to Count) undoes or applies the scaling."""

from __future__ import annotations

import re

import pandas as pd
from fastapi import HTTPException

SCALES = (1, 1000, 1_000_000)
NON_CURRENCY_UNITS = {"%", "percent", "ratio", "x", "count", "number", "days", ""}
_SCALED_COLS = ("value", "budget", "forecast", "price", "budget_price")


_SAYS_SCALED = re.compile(r"thousand|\$ ?000|\(000s?\)|\bmillion|\$m\b", re.I)


def units_unconfirmed(name: str | None, scale, confirmed) -> bool:
    """The dataset's name says thousands or millions, its amounts are stored as entered,
    and nobody has said that's right: its figures probably read 1,000x too small."""
    return int(scale or 1) == 1 and not confirmed and bool(_SAYS_SCALED.search(name or ""))


def ensure_units_confirmed(conn, dataset_id) -> None:
    """Board packs, exports, approval links and approvals stop here until the units are
    confirmed: a warning above a wrong figure still lets the wrong figure out."""
    if dataset_id is None:
        return
    row = conn.execute("SELECT name, amount_scale, units_confirmed FROM datasets WHERE id = ?",
                       (dataset_id,)).fetchone()
    if row and units_unconfirmed(row["name"], row["amount_scale"], row["units_confirmed"]):
        raise HTTPException(status_code=409, detail=(
            "Confirm the units first: this dataset says it's in thousands or millions, but its amounts "
            "are stored as entered. Set 'Amounts are in' from the dataset menu."))


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


def rescale_dataset(conn, dataset_id: int, scale: int) -> int:
    """Set an existing dataset's amounts to units, thousands or millions after import
    (a file 'in thousands' imported before the option existed). Every currency KPI is
    rescaled; drafts written with the old figures are sent back for review, because
    their numbers no longer match. Returns the KPIs rescaled."""
    if scale not in SCALES:
        raise ValueError("amount_scale must be 1, 1000 or 1000000")
    conn.execute("UPDATE datasets SET amount_scale = ? WHERE id = ?", (scale, dataset_id))
    rows = conn.execute(
        """SELECT m.name, k.unit FROM metrics m
           LEFT JOIN kpi_configs k ON k.dataset_id = m.dataset_id AND k.source_metric = m.name
           WHERE m.dataset_id = ?""", (dataset_id,)).fetchall()
    # A saved KPI unit wins; without one, the library's guess (Customers is a count).
    guessed = metric_units(conn, None, [r["name"] for r in rows if not r["unit"]])
    changed = [r["name"] for r in rows
               if align_metric_scale(conn, dataset_id, r["name"], r["unit"] or guessed.get(r["name"]))]
    if changed:
        marks = ",".join("?" * len(changed))
        conn.execute(
            f"""UPDATE generated_reports SET faithfulness = 'failed', review_status = NULL
                WHERE metric_id IN (SELECT id FROM metrics WHERE dataset_id = ? AND name IN ({marks}))""",
            (dataset_id, *changed))
    return len(changed)


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
