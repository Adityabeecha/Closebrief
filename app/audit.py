"""Immutable audit trail (v4.0 compliance / SOC 2 readiness).

Every consequential action (generation, edit, approval, deletion, membership
change) appends one row. Rows are hash-chained per workspace: each row's hash
covers the previous hash + its own content, so any later tampering (edit or
delete of an earlier row) breaks the chain and is detectable via verify().

The app only ever appends — never updates or deletes audit rows.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def _canonical(action: str, entity_type: str, entity_id: str,
               actor_id: str | None, summary: dict, ts: str) -> str:
    return json.dumps(
        {"action": action, "entity_type": entity_type, "entity_id": str(entity_id),
         "actor_id": actor_id, "summary": summary, "ts": ts},
        sort_keys=True, separators=(",", ":"), default=str,
    )


def _row_hash(prev_hash: str, body: str) -> str:
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def _last_hash(conn, workspace_id) -> str:
    row = conn.execute(
        """SELECT row_hash FROM audit_log
           WHERE workspace_id IS ? OR workspace_id = ?
           ORDER BY id DESC LIMIT 1""",
        (workspace_id, workspace_id),
    ).fetchone()
    return row["row_hash"] if row else ""


def record(conn, action: str, entity_type: str, entity_id, *,
           actor_id: str | None = None, actor_email: str | None = None,
           summary: dict | None = None, workspace_id: int | None = None) -> None:
    """Append one audit entry. Best-effort — never raise into the request path.
    The workspace defaults to the current tenant scope."""
    try:
        from app.datasets import current_workspace
        ws = workspace_id if workspace_id is not None else current_workspace()
        ts = datetime.now(timezone.utc).isoformat()
        summary = summary or {}
        body = _canonical(action, entity_type, entity_id, actor_id, summary, ts)
        row_hash = _row_hash(_last_hash(conn, ws), body)
        conn.execute(
            """INSERT INTO audit_log (workspace_id, actor_id, actor_email, action,
                   entity_type, entity_id, summary, row_hash, prev_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ws, actor_id, actor_email, action, entity_type, str(entity_id),
             json.dumps(summary), row_hash, _last_hash(conn, ws), ts),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - auditing must never break the audited action
        pass


def list_entries(conn, workspace_id: int | None, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """SELECT id, actor_id, actor_email, action, entity_type, entity_id, summary,
                  row_hash, created_at
           FROM audit_log WHERE (workspace_id IS ? OR workspace_id = ?)
           ORDER BY id DESC LIMIT ?""",
        (workspace_id, workspace_id, limit),
    ).fetchall()
    return [
        {"id": r["id"], "actor_id": r["actor_id"], "actor_email": r["actor_email"],
         "action": r["action"], "entity_type": r["entity_type"], "entity_id": r["entity_id"],
         "summary": json.loads(r["summary"]) if r["summary"] else {},
         "row_hash": r["row_hash"], "created_at": str(r["created_at"]) if r["created_at"] else None}
        for r in rows
    ]


def verify(conn, workspace_id: int | None) -> dict:
    """Walk the workspace's chain oldest→newest and recompute each hash. Returns
    {ok, checked, first_bad_id}. A broken link means a row was altered or removed."""
    rows = conn.execute(
        """SELECT id, actor_id, action, entity_type, entity_id, summary, row_hash, created_at
           FROM audit_log WHERE (workspace_id IS ? OR workspace_id = ?) ORDER BY id ASC""",
        (workspace_id, workspace_id),
    ).fetchall()
    prev = ""
    for r in rows:
        summary = json.loads(r["summary"]) if r["summary"] else {}
        body = _canonical(r["action"], r["entity_type"], r["entity_id"],
                          r["actor_id"], summary, str(r["created_at"]))
        expected = _row_hash(prev, body)
        if expected != r["row_hash"]:
            return {"ok": False, "checked": len(rows), "first_bad_id": r["id"]}
        prev = r["row_hash"]
    return {"ok": True, "checked": len(rows), "first_bad_id": None}
