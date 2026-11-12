"""Materialize custom/derived KPIs (v5.0).

A derived metric is a formula over other metrics. We evaluate it per period from
the base metrics' computed values and write the result into metric_values, so a
follow-up compute_and_store gives the derived KPI the full deterministic
treatment (deltas, trend, anomaly) — and any narrative about it passes the
faithfulness guard, because its value was computed here, not by the LLM.
"""

from __future__ import annotations

import sqlite3

from app.compute.formula import FormulaError, evaluate, referenced_metrics, validate
from app.datasets import get_or_create_metric, mark_data_changed


def create_derived_metric(conn: sqlite3.Connection, dataset_id: int, name: str, formula: str,
                          unit: str = "USD", category: str = "Derived",
                          direction_good: str = "up") -> int:
    """Validate the formula against the dataset's metrics, store it, and select it
    as a KPI. Caller runs materialize + compute to populate values."""
    refs = validate(formula)   # raises FormulaError on bad syntax / no refs
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM metrics WHERE dataset_id = ?", (dataset_id,)).fetchall()}
    missing = [r for r in refs if r not in have]
    if name in have:
        raise FormulaError("A metric with this name already exists")
    if missing:
        raise FormulaError(f"Formula references unknown metric(s): {', '.join(missing)}")

    cur = conn.execute(
        """INSERT INTO derived_metrics (dataset_id, name, formula, unit, category, direction_good)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (dataset_id, name, formula, unit, category, direction_good),
    )
    did = int(cur.lastrowid)
    # Register it as a metric + a board KPI so it renders like any other. Propagate
    # unit/category/direction onto the metrics row too, so generation (which reads
    # m.unit) sees the real unit and a %-KPI narrative passes the faithfulness guard.
    get_or_create_metric(conn, dataset_id, name)
    conn.execute(
        "UPDATE metrics SET unit = ?, category = ?, direction_good = ? WHERE dataset_id = ? AND name = ?",
        (unit, category, direction_good, dataset_id, name),
    )
    conn.execute(
        """INSERT INTO kpi_configs (dataset_id, source_metric, display_name, category, unit, direction_good)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(dataset_id, source_metric) DO NOTHING""",
        (dataset_id, name, name, category, unit, direction_good),
    )
    conn.commit()
    return did


def refresh(conn: sqlite3.Connection, dataset_id: int) -> None:
    """Recompute base facts, then (re)materialize derived metrics from them and
    recompute again so derived KPIs stay current after any base-data change
    (import, connector sync, manual compute). Safe to call when there are no
    derived metrics (materialize returns 0 → single compute)."""
    from app.compute.kpis import compute_and_store
    compute_and_store(conn, dataset_id)
    if materialize(conn, dataset_id) > 0:
        compute_and_store(conn, dataset_id)


def materialize(conn: sqlite3.Connection, dataset_id: int) -> int:
    """(Re)compute every derived metric's value per period from the base metrics'
    computed_facts, writing metric_values. Returns rows written. Idempotent."""
    derived = conn.execute(
        "SELECT name, formula FROM derived_metrics WHERE dataset_id = ?", (dataset_id,)
    ).fetchall()
    if not derived:
        return 0

    # base metric -> {period: value} for this dataset.
    facts = conn.execute(
        """SELECT m.name AS metric, cf.period, cf.value
           FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
           WHERE m.dataset_id = ?""",
        (dataset_id,),
    ).fetchall()
    names = {d["name"] for d in derived}
    ordered = []
    pending = list(derived)
    while pending:
        ready = [d for d in pending if not (set(referenced_metrics(d["formula"])) & {p["name"] for p in pending})]
        if not ready:
            raise FormulaError("Circular derived metric dependencies")
        ordered.extend(ready)
        pending = [d for d in pending if d not in ready]
    by_metric: dict[str, dict[str, float]] = {}
    for f in facts:
        if f["metric"] in names:
            continue
        by_metric.setdefault(f["metric"], {})[f["period"]] = f["value"]

    all_periods = {p for series in by_metric.values() for p in series}

    written = 0
    for d in ordered:
        metric_id = get_or_create_metric(conn, dataset_id, d["name"])
        conn.execute("DELETE FROM metric_values WHERE metric_id = ?", (metric_id,))
        conn.execute("DELETE FROM computed_facts WHERE metric_id = ?", (metric_id,))
        by_metric[d["name"]] = {}
        refs = referenced_metrics(d["formula"])
        if not refs:
            continue
        # A reference that has values somewhere in the dataset but not in this
        # period is a real zero, not an unknown: an account that was closed
        # mid-year (or opened mid-year) contributes nothing to a sum for the
        # months it does not span. Requiring every reference to be present in
        # every period instead produced an empty series for exactly the
        # reclassification case a "combined" KPI exists to express.
        known = [r for r in refs if by_metric.get(r)]
        if len(known) != len(refs):
            continue
        for period in sorted(all_periods):
            # Missing base observations may represent closed accounts; undefined
            # derived results must instead propagate as unknown.
            if any(r in names and period not in by_metric[r] for r in refs):
                continue
            if not any(period in by_metric[r] for r in refs):
                continue
            vals = {r: by_metric[r].get(period, 0.0) for r in refs}
            v = evaluate(d["formula"], vals)
            if v is None:
                continue
            by_metric[d["name"]][period] = round(float(v), 4)
            conn.execute(
                """INSERT INTO metric_values (metric_id, period, value)
                   VALUES (?, ?, ?)
                   ON CONFLICT(metric_id, period) DO UPDATE SET value = excluded.value""",
                (metric_id, period, round(float(v), 4)),
            )
            written += 1
    mark_data_changed(conn, dataset_id)
    conn.commit()
    return written
