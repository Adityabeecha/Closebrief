"""One mapped dimension, stored atomically by the already lock-checked value writer."""

import json

import pandas as pd


def dimensions(raw):
    return json.loads(raw) if isinstance(raw, str) and raw else {}


def _member(value):
    return str(value).strip() if value is not None and str(value).strip() else "(blank)"


def _sum(group, field):
    values = [float(v) for v in group[field] if pd.notna(v)] if field in group else []
    return sum(values) if values else None


def member_rows(ws, metric_id, period, group, dimension=None) -> list[tuple]:
    """The breakdown rows for one KPI and month, ready to insert (none for fewer than two parts)."""
    if "dimensions" not in group:
        return []
    decoded = [dimensions(raw) for raw in group["dimensions"]]
    dimension = dimension or next((key for row in decoded for key in row), None)
    if dimension is None:
        return []
    members = [_member(row.get(dimension)) for row in decoded]
    if len(set(members)) < 2:
        return []
    grouped = group.copy()
    grouped["_member"] = members
    return [(ws, metric_id, period, dimension, member, _sum(rows, "value"), _sum(rows, "budget"),
             _sum(rows, "forecast"), len(rows)) for member, rows in grouped.groupby("_member")]


INSERT_MEMBERS = """INSERT INTO metric_breakdowns
    (workspace_id, metric_id, period, dimension, member, value, budget, forecast, source_rows)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""


def replace_members(conn, metric_id, period, group, dimension=None):
    conn.execute("DELETE FROM metric_breakdowns WHERE metric_id=? AND period=?", (metric_id, period))
    if "dimensions" not in group:
        return
    decoded = [dimensions(raw) for raw in group["dimensions"]]
    dimension = dimension or next((key for row in decoded for key in row), None)
    if dimension is None:
        return
    members = [_member(row.get(dimension)) for row in decoded]
    if len(set(members)) < 2:
        return
    ws = conn.execute("""SELECT d.workspace_id FROM datasets d JOIN metrics m ON m.dataset_id=d.id
                         WHERE m.id=?""", (metric_id,)).fetchone()["workspace_id"]
    grouped = group.copy()
    grouped["_member"] = members
    for member, rows in grouped.groupby("_member"):
        conn.execute("""INSERT INTO metric_breakdowns
            (workspace_id, metric_id, period, dimension, member, value, budget, forecast, source_rows)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (ws, metric_id, period, dimension, member, _sum(rows, "value"),
                      _sum(rows, "budget"), _sum(rows, "forecast"), len(rows)))


def join_members(conn, metric_id, period, group, field):
    existing = conn.execute("SELECT dimension FROM metric_breakdowns WHERE metric_id=? AND period=? LIMIT 1",
                            (metric_id, period)).fetchone()
    if existing is None:
        return
    dimension = existing["dimension"]
    conn.execute(f"UPDATE metric_breakdowns SET {field}=NULL WHERE metric_id=? AND period=?", (metric_id, period))
    if "dimensions" not in group:
        return
    decoded = [dimensions(raw) for raw in group["dimensions"]]
    if not all(dimension in row for row in decoded):
        return
    grouped = group.copy()
    grouped["_member"] = [_member(row.get(dimension)) for row in decoded]
    for member, rows in grouped.groupby("_member"):
        conn.execute(f"UPDATE metric_breakdowns SET {field}=? WHERE metric_id=? AND period=? AND member=?",
                     (_sum(rows, "value"), metric_id, period, member))
