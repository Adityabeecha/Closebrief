"""Materialize custom/derived KPIs (v5.0).

A derived metric is a formula over other metrics. We evaluate it per period from
the base metrics' computed values and write the result into metric_values, so a
follow-up compute_and_store gives the derived KPI the full deterministic
treatment (deltas, trend, anomaly) — and any narrative about it passes the
faithfulness guard, because its value was computed here, not by the LLM.
"""

from __future__ import annotations

import sqlite3

from app.compute.formula import evaluate, referenced_metrics, validate
from app.datasets import get_or_create_metric


def create_derived_metric(conn: sqlite3.Connection, dataset_id: int, name: str, formula: str,
                          unit: str = "USD", category: str = "Derived",
                          direction_good: str = "up") -> int:
    """Validate the formula against the dataset's metrics, store it, and select it
    as a KPI. Caller runs materialize + compute to populate values."""
    refs = validate(formula)   # raises FormulaError on bad syntax / no refs
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM metrics WHERE dataset_id = ?", (dataset_id,)).fetchall()}
    missing = [r for r in refs if r not in have]
    if missing:
        from app.compute.formula import FormulaError
        raise FormulaError(f"Formula references unknown metric(s): {', '.join(missing)}")

    cur = conn.execute(
        """INSERT INTO derived_metrics (dataset_id, name, formula, unit, category, direction_good)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (dataset_id, name, formula, unit, category, direction_good),
    )
    did = int(cur.lastrowid)
    # Register it as a metric + a board KPI so it renders like any other.
    get_or_create_metric(conn, dataset_id, name)
    conn.execute(
        """INSERT INTO kpi_configs (dataset_id, source_metric, display_name, category, unit, direction_good)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(dataset_id, source_metric) DO NOTHING""",
        (dataset_id, name, name, category, unit, direction_good),
    )
    conn.commit()
    return did


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
    by_metric: dict[str, dict[str, float]] = {}
    for f in facts:
        by_metric.setdefault(f["metric"], {})[f["period"]] = f["value"]

    written = 0
    for d in derived:
        metric_id = get_or_create_metric(conn, dataset_id, d["name"])
        refs = referenced_metrics(d["formula"])
        # Periods where every referenced metric has a value.
        periods = set.intersection(*[set(by_metric.get(r, {})) for r in refs]) if refs else set()
        for period in sorted(periods):
            vals = {r: by_metric[r][period] for r in refs}
            v = evaluate(d["formula"], vals)
            if v is None:
                continue
            conn.execute(
                """INSERT INTO metric_values (metric_id, period, value)
                   VALUES (?, ?, ?)
                   ON CONFLICT(metric_id, period) DO UPDATE SET value = excluded.value""",
                (metric_id, period, round(float(v), 4)),
            )
            written += 1
    conn.commit()
    return written
