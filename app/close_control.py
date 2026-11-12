"""Shared transactional change tracking and month locks for all value writers."""

from datetime import datetime, timezone

from fastapi import HTTPException

from app.datasets import timestamp_text

LOCKED_COMMENTARY = "This close is locked. Unlock it to change commentary."


def serialize_writes(conn, dataset_id):
    # Serialize data writers and lock/unlock on the dataset, including empty months.
    if getattr(conn, "backend", "sqlite") == "postgres":
        conn.execute("SELECT id FROM datasets WHERE id = ? FOR UPDATE", (dataset_id,)).fetchone()
    elif not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def snapshot(conn, dataset_id):
    serialize_writes(conn, dataset_id)
    return {(r["metric_id"], r["period"]): dict(r) for r in conn.execute(
        """SELECT mv.* FROM metric_values mv JOIN metrics m ON m.id=mv.metric_id
           WHERE m.dataset_id=?""", (dataset_id,)).fetchall()}


def locked_months(conn, dataset_id):
    return {r["period"] for r in conn.execute(
        "SELECT period FROM close_locks WHERE dataset_id=?", (dataset_id,)).fetchall()}


def record_changes(conn, dataset_id, before, source, *, actor_id=None, actor_email=None):
    # A brand-new dataset has no prior values. Its first receipt covers the import.
    if not before:
        return
    after = snapshot(conn, dataset_id)
    ws = conn.execute("SELECT workspace_id FROM datasets WHERE id=?", (dataset_id,)).fetchone()["workspace_id"]
    at = datetime.now(timezone.utc).isoformat()
    for mid, period in sorted(before.keys() | after.keys()):
        old, new = before.get((mid, period), {}), after.get((mid, period), {})
        for field in ("value", "budget"):
            a, b = old.get(field), new.get(field)
            changed = (a is None) != (b is None) or (a is not None and b is not None and abs(a - b) > 1e-9)
            if changed:
                conn.execute(
                    """INSERT INTO value_changes (workspace_id, dataset_id, metric_id, period, field,
                       old_value, new_value, source, actor_id, actor_email, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ws, dataset_id, mid, period, field, a, b, source, actor_id, actor_email, at))


def skipped_report(counts):
    return [{"period": p, "rows": int(n)} for p, n in sorted(counts.items()) if n]


def ensure_commentary_unlocked(conn, dataset_id, period, *, serialize=True):
    """Refuse commentary writes to a locked month. Pass serialize=False for an early,
    read-only check on a connection that then does slow work (an LLM call): taking the
    write lock there would hold it for the whole call and queue every other writer."""
    if dataset_id is None:
        return
    if serialize:
        serialize_writes(conn, dataset_id)
    if period in locked_months(conn, dataset_id):
        raise HTTPException(status_code=409, detail=LOCKED_COMMENTARY)


def ensure_report_unlocked(conn, report_id):
    row = conn.execute("""SELECT m.dataset_id, gr.period FROM generated_reports gr
                          JOIN metrics m ON m.id=gr.metric_id WHERE gr.id=?""", (report_id,)).fetchone()
    if row:
        ensure_commentary_unlocked(conn, row["dataset_id"], row["period"])


def lock_status(conn, dataset_id, months):
    rows = [r for r in conn.execute("SELECT * FROM close_locks WHERE dataset_id=?", (dataset_id,)).fetchall()
            if r["period"] in months]
    locked = bool(months) and len(rows) == len(months)
    latest = max(rows, key=lambda r: timestamp_text(r["locked_at"])) if locked else None
    return {"locked": locked, "locked_at": timestamp_text(latest["locked_at"]) if latest else None,
            "locked_by": (latest["locked_by_email"] or latest["locked_by_id"] or "System") if latest else None}
