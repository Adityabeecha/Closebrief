"""Collaborative review workflow (v5.0): assign a narrative to a reviewer, track
approval status, and keep version history with diffs.

Version snapshots are appended whenever a narrative is edited; the diff between
consecutive versions is computed on read (difflib), so history is a faithful,
auditable record of who changed what.
"""

from __future__ import annotations

import difflib

REVIEW_STATUSES = ("pending", "approved", "changes_requested")


def add_version(conn, report_id: int, narrative: str,
                editor_id: str | None, editor_email: str | None) -> int:
    """Append a version snapshot for a report. Version numbers are 1-based."""
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM report_versions WHERE report_id = ?",
        (report_id,),
    ).fetchone()
    version = int(row["v"]) + 1
    conn.execute(
        """INSERT INTO report_versions (report_id, version, narrative, editor_id, editor_email)
           VALUES (?, ?, ?, ?, ?)""",
        (report_id, version, narrative, editor_id, editor_email),
    )
    conn.commit()
    return version


def assign(conn, report_id: int, user_id: str | None, email: str | None) -> None:
    conn.execute(
        "UPDATE generated_reports SET assigned_to = ?, assigned_email = ?, review_status = 'pending' WHERE id = ?",
        (user_id, email, report_id),
    )
    conn.commit()


def set_status(conn, report_id: int, status: str) -> None:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"status must be one of {REVIEW_STATUSES}")
    conn.execute(
        "UPDATE generated_reports SET review_status = ? WHERE id = ?", (status, report_id))
    conn.commit()


def list_versions(conn, report_id: int) -> list[dict]:
    """Versions oldest→newest, each with a unified diff from the previous one."""
    rows = conn.execute(
        """SELECT version, narrative, editor_id, editor_email, created_at
           FROM report_versions WHERE report_id = ? ORDER BY version ASC""",
        (report_id,),
    ).fetchall()
    out, prev = [], ""
    for r in rows:
        text = r["narrative"] or ""
        diff = list(difflib.unified_diff(
            prev.splitlines(), text.splitlines(),
            fromfile=f"v{r['version'] - 1}", tofile=f"v{r['version']}", lineterm=""))
        out.append({
            "version": r["version"], "narrative": text,
            "editor_id": r["editor_id"], "editor_email": r["editor_email"],
            "created_at": str(r["created_at"]) if r["created_at"] else None,
            "diff": diff,
        })
        prev = text
    return out
