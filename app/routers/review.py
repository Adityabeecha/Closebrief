"""Collaborative review workflow (v5.0/5.2): assign a narrative to a reviewer,
record approval, list version diffs, and the reviewer inbox. Assignment sends a
best-effort email nudge with a deep link."""

from fastapi import APIRouter, Depends, HTTPException

from app import audit
from app import review as review_svc
from app.api import CurrentUser, require_member, require_read
from app.config import settings
from app.datasets import _scope_pred, active_dataset_id
from app.db import get_connection
from app.services import bg_executor

router = APIRouter(tags=["review"])


def _email_review_nudge_async(to_email: str | None, metric: str, period: str,
                              assigned_by: str | None) -> None:
    """Email a reviewer that a narrative was assigned to them, on the background
    pool (best-effort; delivery must never block or fail the assign request)."""
    if not to_email:
        return

    def _run():
        try:
            import urllib.parse

            from app.notifications.channels import EmailChannel
            who = assigned_by or "A teammate"
            subject = f"[Closebrief] Review requested: {metric} ({period})"
            base = (settings.app_base_url or "").rstrip("/")
            link = ""
            if base:
                url = f"{base}/#metric={urllib.parse.quote(metric)}&period={urllib.parse.quote(period)}"
                link = (f"<p><a href='{url}' style='display:inline-block;background:#1e6e50;color:#fff;"
                        f"padding:9px 16px;border-radius:8px;text-decoration:none;font-weight:600'>"
                        f"Review in Closebrief</a></p>")
            html = (
                f"<p>{who} assigned you a narrative to review in Closebrief.</p>"
                f"<p style='font-size:16px'><b>{metric}</b> — {period}</p>"
                f"{link or '<p>Open Closebrief to approve it or request changes.</p>'}"
            )
            EmailChannel({"recipients": [to_email]})._send(subject, html)
        except Exception:  # noqa: BLE001 - best-effort by design
            pass

    bg_executor.submit(_run)


def _scoped_report(conn, report_id: int):
    return conn.execute(
        f"""SELECT gr.id FROM generated_reports gr
            JOIN metrics m ON m.id = gr.metric_id JOIN datasets d ON d.id = m.dataset_id
            WHERE gr.id = ? AND {_scope_pred('d')}""",
        (report_id,),
    ).fetchone()


@router.post("/reports/{report_id}/assign")
def assign_report(report_id: int, payload: dict, user: CurrentUser = Depends(require_member)) -> dict:
    """Assign a narrative to a reviewer (status → pending) and email them a nudge."""
    email = (payload.get("email") or "").strip() or None
    conn = get_connection()
    try:
        if _scoped_report(conn, report_id) is None:
            raise HTTPException(status_code=404, detail="Report not found")
        review_svc.assign(conn, report_id, payload.get("user_id"), email)
        row = conn.execute(
            """SELECT m.name AS metric, gr.period FROM generated_reports gr
               JOIN metrics m ON m.id = gr.metric_id WHERE gr.id = ?""",
            (report_id,),
        ).fetchone()
        audit.record(conn, "assign", "report", report_id, actor_id=user.id, actor_email=user.email,
                     summary={"assigned_email": email})
    finally:
        conn.close()
    if email and row:
        _email_review_nudge_async(email, row["metric"], row["period"], user.email)
    return {"report_id": report_id, "review_status": "pending", "assigned_email": email}


@router.post("/reports/{report_id}/review")
def review_report(report_id: int, payload: dict, user: CurrentUser = Depends(require_member)) -> dict:
    """Record a review decision (approved | changes_requested)."""
    status = (payload.get("status") or "").strip()
    if status not in review_svc.REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {review_svc.REVIEW_STATUSES}")
    conn = get_connection()
    try:
        if _scoped_report(conn, report_id) is None:
            raise HTTPException(status_code=404, detail="Report not found")
        review_svc.set_status(conn, report_id, status)
        audit.record(conn, status, "report", report_id, actor_id=user.id, actor_email=user.email,
                     summary={"note": payload.get("note")})
        return {"report_id": report_id, "review_status": status}
    finally:
        conn.close()


@router.get("/reports/review-queue")
def review_queue(all_datasets: bool = False,
                 user: CurrentUser = Depends(require_read)) -> list[dict]:
    """Narratives assigned to the current user and still pending review. Defaults
    to the active dataset (so opening one lands on its card); all_datasets=true
    spans the workspace, and each item carries its dataset so the UI can switch to
    it on open. Drives the review inbox and its unread badge."""
    conn = get_connection()
    try:
        base = (
            """SELECT gr.id AS report_id, m.name AS metric, gr.period,
                      gr.review_status, gr.assigned_email, gr.narrative,
                      d.id AS dataset_id, d.name AS dataset_name
               FROM generated_reports gr
               JOIN metrics m ON m.id = gr.metric_id
               JOIN datasets d ON d.id = m.dataset_id
               WHERE gr.review_status = 'pending'
                 AND (gr.assigned_to = ? OR gr.assigned_email = ?) AND """
        )
        if all_datasets:
            rows = conn.execute(
                base + _scope_pred("d") + " ORDER BY gr.id DESC",
                (user.id, user.email),
            ).fetchall()
        else:
            ds = active_dataset_id(conn)
            if ds is None:
                return []
            rows = conn.execute(
                base + "d.id = ? ORDER BY gr.id DESC",
                (user.id, user.email, ds),
            ).fetchall()
        return [{
            "report_id": r["report_id"], "metric": r["metric"], "period": r["period"],
            "review_status": r["review_status"], "assigned_email": r["assigned_email"],
            "dataset_id": r["dataset_id"], "dataset_name": r["dataset_name"],
            "preview": (r["narrative"] or "")[:160],
        } for r in rows]
    finally:
        conn.close()


@router.get("/reports/{report_id}/versions")
def report_versions(report_id: int, _: CurrentUser = Depends(require_read)) -> list[dict]:
    """Version history of a narrative with a unified diff between each version."""
    conn = get_connection()
    try:
        if _scoped_report(conn, report_id) is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return review_svc.list_versions(conn, report_id)
    finally:
        conn.close()
