"""How often a dataset reports: monthly, quarterly or yearly.

A file of quarter-end rows (2025-03, 2025-06, ...) is a quarterly close: its prior
period is three months back, year to date counts quarters, and the screens say "vs
last quarter". Detected from the gaps between periods that have data."""

from __future__ import annotations

from functools import reduce
from math import gcd

LABELS = {1: "month", 3: "quarter", 12: "year"}


def month_index(period: str) -> int:
    return int(period[:4]) * 12 + int(period[5:7]) - 1


def step_of(months) -> int:
    """1 (monthly), 3 (quarterly) or 12 (yearly) from sorted month indexes."""
    ms = sorted(set(months))
    if len(ms) < 3:   # two points a year apart aren't a yearly file yet
        return 1
    g = reduce(gcd, (b - a for a, b in zip(ms, ms[1:])))
    return 12 if g % 12 == 0 else 3 if g % 3 == 0 else 1


def dataset_step(conn, dataset_id) -> int:
    """The dataset's cadence: the finest step any of its KPIs reports at."""
    rows = conn.execute(
        """SELECT m.id, mv.period FROM metric_values mv JOIN metrics m ON m.id = mv.metric_id
           WHERE m.dataset_id = ?""", (dataset_id,)).fetchall()
    by_metric: dict[int, list[int]] = {}
    for r in rows:
        p = str(r["period"])
        if len(p) >= 7 and p[4] == "-":
            by_metric.setdefault(r["id"], []).append(month_index(p))
    steps = [step_of(v) for v in by_metric.values() if len(v) >= 3]
    return min(steps) if steps else 1


def shift_period(period: str, months: int) -> str:
    """'2026-09' shifted by -3 -> '2026-06'."""
    idx = month_index(period) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def steps_from_values(values_by_metric: dict) -> int:
    """dataset_step for values already loaded ({metric_id: [rows with 'period']})."""
    steps = []
    for rows in values_by_metric.values():
        months = [month_index(str(r["period"])) for r in rows
                  if len(str(r["period"])) >= 7 and str(r["period"])[4] == "-"]
        if len(months) >= 3:
            steps.append(step_of(months))
    return min(steps) if steps else 1
