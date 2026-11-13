"""HTTP routes moved from app.main: ops."""

import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from app import audit, connectors, review, scheduling
from app.api import require_admin, require_read, require_write  # noqa: E402
from app.auth import CurrentUser
from app.close_control import ensure_report_unlocked
from app.config import resolved_vector_backend, settings
from app.datasets import (
    _scope_pred,
    current_workspace,
)
from app.db import get_connection
from app.deps import shared_cache, shared_vector_store
from app.generation.guard import figures_not_in
from app.generation.llm_client import get_llm_client
from app.routes_common import _ALERT_LATENCIES, _LATENCIES
from app.schemas import (
    FeedbackIn,
)

router = APIRouter()


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))
    return round(ordered[idx], 1)


@router.get("/health")
def health() -> dict:
    """v1.1 Phase 4: report DB, vector store, and cache status, not just 200."""
    checks: dict[str, dict] = {}

    db_backend = "postgres" if settings.database_url else "sqlite"
    try:
        conn = get_connection()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        checks["db"] = {"backend": db_backend, "ok": True}
    except Exception as exc:
        checks["db"] = {"backend": db_backend, "ok": False, "error": str(exc)[:120]}

    try:
        vs = shared_vector_store()
        checks["vector_store"] = {
            "backend": getattr(vs, "backend", "faiss"),
            "ok": True,
            "vectors": vs.size,
        }
    except Exception as exc:
        checks["vector_store"] = {"backend": resolved_vector_backend(), "ok": False, "error": str(exc)[:120]}

    cache = shared_cache()
    checks["cache"] = {"backend": cache.backend, "ok": cache.ping()}

    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "environment": settings.environment,
        **checks,
    }


@router.get("/costs")
def costs(_: CurrentUser = Depends(require_admin)) -> dict:
    """v1.2: spend by day and endpoint (from llm_calls), request latency
    p50/p95 (in-process ring buffer), and cache hit rate."""
    conn = get_connection()
    try:
        if getattr(conn, "backend", "sqlite") == "postgres":
            day_expr = "to_char(created_at, 'YYYY-MM-DD')"
        else:
            day_expr = "date(created_at)"
        by_day = [
            {**dict(r), "cost_usd": round(float(r["cost_usd"] or 0), 6)}
            for r in conn.execute(
                f"""SELECT {day_expr} AS day, endpoint,
                        COUNT(*) AS calls,
                        SUM(COALESCE(prompt_tokens,0)) AS prompt_tokens,
                        SUM(COALESCE(completion_tokens,0)) AS completion_tokens,
                        SUM(COALESCE(cost_usd,0)) AS cost_usd
                    FROM llm_calls GROUP BY {day_expr}, endpoint
                    ORDER BY day DESC, endpoint"""
            ).fetchall()
        ]
        total = conn.execute(
            "SELECT COUNT(*) AS calls, SUM(COALESCE(cost_usd,0)) AS cost_usd FROM llm_calls"
        ).fetchone()
    finally:
        conn.close()

    latency = {
        path: {"p50_ms": _percentile(list(v), 50), "p95_ms": _percentile(list(v), 95), "n": len(v)}
        for path, v in _LATENCIES.items()
        if v and path in ("/generate-insight", "/digest", "/facts")
    }
    alert_samples = list(_ALERT_LATENCIES)
    alert_latency = {
        "p50_ms": _percentile(alert_samples, 50) if alert_samples else None,
        "p95_ms": _percentile(alert_samples, 95) if alert_samples else None,
        "n": len(alert_samples),
        # The success criterion: anomaly alert reaches channels < 30s from compute.
        "within_30s": (max(alert_samples) <= 30000) if alert_samples else None,
    }
    return {
        "total_llm_calls": total["calls"] or 0,
        "total_cost_usd": round(total["cost_usd"] or 0.0, 6),
        "by_day": by_day,
        "latency": latency,
        "alert_latency": alert_latency,
        "cache": shared_cache().stats(),
    }


@router.get("/schedules")
def list_schedules(_: CurrentUser = Depends(require_admin)) -> list[dict]:
    conn = get_connection()
    try:
        return scheduling.list_jobs(conn)
    finally:
        conn.close()


@router.post("/schedules", status_code=201)
def create_schedule(payload: dict, _: CurrentUser = Depends(require_admin)) -> dict:
    kind = (payload.get("kind") or "").strip()
    cadence = (payload.get("cadence") or "").strip()
    if kind not in scheduling.KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {scheduling.KINDS}")
    if cadence not in scheduling.CADENCES:
        raise HTTPException(status_code=422, detail=f"cadence must be one of {scheduling.CADENCES}")
    conn = get_connection()
    try:
        if kind == "digest":
            config = {"top_n": int(payload.get("top_n", 5))}
        elif kind == "board_pack":
            recipients = [e.strip() for e in (payload.get("recipients") or []) if e and e.strip()]
            config = {"recipients": recipients}
        else:
            config = {}
        job_id = scheduling.create_job(
            conn, kind, cadence,
            dataset_id=payload.get("dataset_id"),
            config=config,
            enabled=bool(payload.get("enabled", True)),
        )
        return {"id": job_id}
    finally:
        conn.close()


@router.patch("/schedules/{job_id}")
def update_schedule(job_id: int, payload: dict, _: CurrentUser = Depends(require_admin)) -> dict:
    conn = get_connection()
    try:
        scheduling.set_job_enabled(conn, job_id, bool(payload.get("enabled", True)))
        return {"id": job_id}
    finally:
        conn.close()


@router.delete("/schedules/{job_id}", status_code=204)
def remove_schedule(job_id: int, _: CurrentUser = Depends(require_admin)) -> None:
    conn = get_connection()
    try:
        if not scheduling.delete_job(conn, job_id):
            raise HTTPException(status_code=404, detail=f"Schedule {job_id} not found")
    finally:
        conn.close()


@router.post("/internal/scheduler/tick")
def scheduler_tick(request: Request) -> dict:
    """Cron entry point. Gated by the shared SCHEDULER_TOKEN (no user JWT), so an
    external pinger can drive scheduled digests + anomaly scans. Disabled (503)
    until SCHEDULER_TOKEN is configured."""
    if not settings.scheduler_token:
        raise HTTPException(status_code=503, detail="Scheduling is not configured")
    token = request.headers.get("x-scheduler-token", "")
    if not hmac.compare_digest(token, settings.scheduler_token):
        raise HTTPException(status_code=401, detail="Invalid scheduler token")
    try:
        llm_client = get_llm_client()
    except Exception:  # noqa: BLE001 - anomaly scans still run deterministically without an LLM
        llm_client = None
    conn = get_connection()
    try:
        return scheduling.run_due_jobs(conn, llm_client=llm_client)
    finally:
        conn.close()


@router.post("/feedback", status_code=201)
def feedback_endpoint(fb: FeedbackIn, user: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        # Scope the report to the caller's universe (demo vs real) so feedback can
        # never reach across it — the report's dataset must match the request scope.
        exists = conn.execute(
            f"""SELECT gr.narrative, gr.sources, gr.review_status, gr.faithfulness FROM generated_reports gr
                JOIN metrics m ON m.id = gr.metric_id
                JOIN datasets d ON d.id = m.dataset_id
                WHERE gr.id = ? AND {_scope_pred('d')}""",
            (fb.report_id,),
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Report {fb.report_id} not found")
        ensure_report_unlocked(conn, fb.report_id)
        cur = conn.execute(
            """INSERT INTO feedback (report_id, action, edited_text, reason, user_id, user_email)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (fb.report_id, fb.action, fb.edited_text, fb.reason, user.id, user.email),
        )
        # Version history (v5.0): an edit snapshots a new version. The first edit
        # also captures the original (v1) so the diff chain starts from generation.
        if fb.action == "edited" and fb.edited_text:
            have = conn.execute(
                "SELECT COUNT(*) AS n FROM report_versions WHERE report_id = ?", (fb.report_id,)
            ).fetchone()["n"]
            if have == 0 and exists["narrative"]:
                review.add_version(conn, fb.report_id, exists["narrative"], None, "original", commit=False)
            review.add_version(conn, fb.report_id, fb.edited_text, user.id, user.email, commit=False)
            # An edit keeps "Figures checked" only if every figure in it was in the checked
            # draft or a note it cited; a new number flags the draft. An approved draft that
            # is edited goes back for approval.
            cited = [int(x["id"]) for x in json.loads(exists["sources"] or "[]") if str(x.get("id", "")).isdigit()]
            bodies = [r["body"] for r in conn.execute(
                f"SELECT body FROM context_documents WHERE id IN ({','.join('?' * len(cited))})", tuple(cited)
            ).fetchall()] if cited else []
            unverified = figures_not_in(fb.edited_text, [exists["narrative"], *bodies])
            conn.execute(
                "UPDATE generated_reports SET faithfulness = ?, review_status = ? WHERE id = ?",
                ("failed" if unverified else exists["faithfulness"],
                 None if exists["review_status"] == "approved" else exists["review_status"], fb.report_id),
            )
        conn.commit()
        audit.record(conn, fb.action, "report", fb.report_id, actor_id=user.id,
                     actor_email=user.email, summary={"edited": bool(fb.edited_text)})
        out = {"feedback_id": int(cur.lastrowid)}
        if fb.action == "edited" and fb.edited_text:
            out["unverified"] = unverified
        return out
    finally:
        conn.close()


@router.get("/connectors")
def list_connectors_endpoint(_: CurrentUser = Depends(require_read)) -> list[dict]:
    conn = get_connection()
    try:
        return connectors.list_connectors(conn, current_workspace())
    finally:
        conn.close()


@router.post("/connectors", status_code=201)
def create_connector_endpoint(payload: dict, user: CurrentUser = Depends(require_write)) -> dict:
    kind = (payload.get("kind") or "").strip()
    name = (payload.get("name") or "").strip()
    if kind not in connectors.KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {connectors.KINDS}")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    conn = get_connection()
    try:
        try:
            cid = connectors.create_connector(
                conn, kind, name, payload.get("config") or {},
                payload.get("dataset_name") or name, current_workspace())
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        audit.record(conn, "create", "connector", cid, actor_id=user.id,
                     actor_email=user.email, summary={"kind": kind, "name": name})
        return {"id": cid}
    finally:
        conn.close()


@router.post("/connectors/{connector_id}/sync")
def sync_connector_endpoint(connector_id: int, user: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        try:
            result = connectors.sync_connector(conn, connector_id, current_workspace(),
                                               actor_id=user.id, actor_email=user.email)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        audit.record(conn, "sync", "connector", connector_id, actor_id=user.id,
                     actor_email=user.email, summary=result)
        return result
    finally:
        conn.close()


@router.delete("/connectors/{connector_id}", status_code=204)
def delete_connector_endpoint(connector_id: int, _: CurrentUser = Depends(require_write)) -> None:
    conn = get_connection()
    try:
        if not connectors.delete_connector(conn, connector_id, current_workspace()):
            raise HTTPException(status_code=404, detail=f"Connector {connector_id} not found")
    finally:
        conn.close()


@router.get("/audit")
def audit_list(_: CurrentUser = Depends(require_read)) -> list[dict]:
    """The active workspace's audit trail (recent first). Admin-relevant but
    readable by any member for transparency."""
    conn = get_connection()
    try:
        entries = audit.list_entries(conn, current_workspace())
        # Say which KPI and close a report entry is about, not just its id.
        ids = sorted({int(e["entity_id"]) for e in entries
                      if e["entity_type"] == "report" and str(e["entity_id"]).isdigit()})
        if ids:
            marks = ",".join("?" * len(ids))
            names = {r["id"]: (r["name"], r["period"]) for r in conn.execute(
                f"SELECT gr.id, m.name, gr.period FROM generated_reports gr JOIN metrics m ON m.id = gr.metric_id "
                f"WHERE gr.id IN ({marks})", ids).fetchall()}
            for e in entries:
                hit = names.get(int(e["entity_id"])) if str(e["entity_id"]).isdigit() else None
                if e["entity_type"] == "report" and hit:
                    e["metric"], e["period"] = hit
        return entries
    finally:
        conn.close()


@router.get("/audit/verify")
def audit_verify(_: CurrentUser = Depends(require_admin)) -> dict:
    """Recompute the hash chain to prove the trail hasn't been tampered with."""
    conn = get_connection()
    try:
        return audit.verify(conn, current_workspace())
    finally:
        conn.close()
