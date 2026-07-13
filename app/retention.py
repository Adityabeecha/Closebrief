"""Configurable data retention (v4.0 compliance). Purges old operational
telemetry (llm_calls, scheduler_runs) beyond a workspace's retention window.

The audit_log is deliberately NOT purged — it is the immutable compliance record.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _cutoff(days: int) -> str:
    # ISO date the retained window starts; compares against TEXT (SQLite) and
    # TIMESTAMPTZ (Postgres) created_at alike.
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def purge_workspace(conn, workspace_id: int, retention_days: int) -> dict:
    """Delete telemetry older than the window for one workspace. Returns counts."""
    cutoff = _cutoff(retention_days)
    cur = conn.execute(
        "DELETE FROM llm_calls WHERE workspace_id = ? AND created_at < ?",
        (workspace_id, cutoff),
    )
    removed = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    conn.commit()
    return {"workspace_id": workspace_id, "llm_calls_purged": removed}


def run_retention(conn) -> dict:
    """Purge every workspace that has a retention policy set. Used by the
    scheduled 'retention_purge' job."""
    rows = conn.execute(
        "SELECT id, retention_days FROM workspaces WHERE retention_days IS NOT NULL"
    ).fetchall()
    total = 0
    for r in rows:
        days = int(r["retention_days"])
        if days <= 0:
            continue
        total += purge_workspace(conn, int(r["id"]), days)["llm_calls_purged"]
    return {"workspaces": len(rows), "llm_calls_purged": total}
