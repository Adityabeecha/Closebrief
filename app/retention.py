"""Configurable data retention (v4.0 compliance). Purges old operational
telemetry (llm_calls, scheduler_runs) beyond a workspace's retention window.

The audit_log is deliberately NOT purged — it is the immutable compliance record.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import resolved_vector_backend, settings
from app.ingestion.upload import purge_old_uploads
from app.workspaces import invalidate_membership_cache


def _cutoff(days: int) -> str:
    # ISO date the retained window starts; compares against TEXT (SQLite) and
    # TIMESTAMPTZ (Postgres) created_at alike.
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def purge_workspace(conn, workspace_id: int, retention_days: int) -> dict:
    """Delete telemetry older than the window for one workspace. Returns counts."""
    cutoff = _cutoff(retention_days)
    # Count before delete — the Postgres cursor wrapper does not expose rowcount.
    removed = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_calls WHERE workspace_id = ? AND created_at < ?",
        (workspace_id, cutoff),
    ).fetchone()["n"]
    conn.execute(
        "DELETE FROM llm_calls WHERE workspace_id = ? AND created_at < ?",
        (workspace_id, cutoff),
    )
    conn.commit()
    return {"workspace_id": workspace_id, "llm_calls_purged": int(removed)}


def purge_inactive_sandboxes(conn, days: int = 30, keep_user_ids=()) -> dict:
    """Delete visitor workspaces whose latest stored activity predates the cutoff.

    Activity is MAX(created_at) across datasets, reports (via dataset metrics),
    context documents and LLM calls. Empty workspaces use their creation time.
    Global admins, keep_user_ids members, retention_days <= 0 and demo roots
    protect a workspace. The immutable audit_log and app_roles are retained.
    """
    result = {"workspaces_purged": 0, "datasets_purged": 0}
    if days <= 0:
        return result
    from app import datasets

    keep_user_ids = set(keep_user_ids)
    # Second safeguard for the owner: emails listed as admins (GOOGLE_ADMIN_EMAILS)
    # keep their workspaces even if their stored role were somehow not 'admin'.
    keep_emails = {e.strip().casefold() for e in (settings.google_admin_emails or "").split(",") if e.strip()}
    candidates = conn.execute(
        """SELECT w.id FROM workspaces w
           WHERE (w.retention_days IS NULL OR w.retention_days > 0)
           AND NOT EXISTS (
               SELECT 1 FROM workspace_members wm JOIN app_roles ar ON CAST(ar.user_id AS TEXT) = wm.user_id
               WHERE wm.workspace_id = w.id AND ar.role = 'admin')
           AND NOT EXISTS (SELECT 1 FROM datasets d WHERE d.workspace_id = w.id AND d.is_demo = true)
           AND NOT EXISTS (SELECT 1 FROM context_documents c WHERE c.workspace_id = w.id AND c.is_demo = true)
           AND COALESCE((
               SELECT MAX(activity.created_at) FROM (
                   SELECT workspace_id, created_at FROM datasets
                   UNION ALL
                   SELECT d.workspace_id, r.created_at FROM generated_reports r
                   JOIN metrics m ON m.id = r.metric_id JOIN datasets d ON d.id = m.dataset_id
                   UNION ALL
                   SELECT workspace_id, created_at FROM context_documents
                   UNION ALL
                   SELECT workspace_id, created_at FROM llm_calls
               ) activity WHERE activity.workspace_id = w.id
           ), w.created_at) < ?""",
        (_cutoff(days),),
    ).fetchall()
    previous_workspace, previous_demo = datasets.current_workspace(), datasets.is_demo_scope()
    try:
        datasets.set_demo_scope(False)
        for row in candidates:
            ws = int(row["id"])
            members = conn.execute("SELECT user_id, email FROM workspace_members WHERE workspace_id = ?", (ws,)).fetchall()
            if any(str(member["user_id"]) in keep_user_ids
                   or (member["email"] or "").strip().casefold() in keep_emails for member in members):
                continue
            datasets.set_workspace_scope(ws)
            owned = conn.execute("SELECT id FROM datasets WHERE workspace_id = ? AND COALESCE(is_demo, false) = false",
                                 (ws,)).fetchall()
            for dataset in owned:
                ds = int(dataset["id"])
                if not datasets.delete_dataset(conn, ds):
                    raise RuntimeError(f"Sandbox dataset {ds} could not be deleted in workspace {ws}")
                result["datasets_purged"] += 1
            documents = conn.execute("SELECT id FROM context_documents WHERE workspace_id = ?", (ws,)).fetchall()
            uploads = conn.execute("SELECT file_path FROM uploads WHERE workspace_id = ?", (ws,)).fetchall()
            for upload in uploads:
                if upload["file_path"]:
                    Path(upload["file_path"]).unlink(missing_ok=True)
            for table in ("context_documents", "connectors", "notification_configs", "uploads",
                          "workspace_invites", "workspace_members", "llm_calls"):
                conn.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (ws,))
            conn.execute("DELETE FROM workspaces WHERE id = ?", (ws,))
            conn.commit()
            for member in members:
                invalidate_membership_cache(str(member["user_id"]))
            # pgvector rows cascade with context_documents; FAISS needs removal.
            if documents and resolved_vector_backend() == "faiss":
                from app.deps import shared_vector_store
                vectors = shared_vector_store()
                vectors.remove([int(doc["id"]) for doc in documents])
                vectors.persist()
            result["workspaces_purged"] += 1
    except Exception:
        conn.rollback()
        raise
    finally:
        datasets.set_workspace_scope(previous_workspace)
        datasets.set_demo_scope(previous_demo)
    return result


def run_retention(conn) -> dict:
    """Expire inactive sandboxes, configured telemetry and uploads older than 7 days.
    Used by the scheduled 'retention_purge' job."""
    # Check activity before telemetry purging removes any evidence of recent use.
    sandboxes = purge_inactive_sandboxes(conn, days=settings.sandbox_retention_days)
    rows = conn.execute(
        "SELECT id, retention_days FROM workspaces WHERE retention_days IS NOT NULL"
    ).fetchall()
    total = 0
    for r in rows:
        days = int(r["retention_days"])
        if days <= 0:
            continue
        total += purge_workspace(conn, int(r["id"]), days)["llm_calls_purged"]
    uploads_purged = purge_old_uploads(conn)
    return {"workspaces": len(rows), "llm_calls_purged": total, "uploads_purged": uploads_purged,
            "sandboxes_purged": sandboxes["workspaces_purged"], "datasets_purged": sandboxes["datasets_purged"]}
