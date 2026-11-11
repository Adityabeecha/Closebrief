"""Cross-cutting request helpers shared by main and the feature routers: the
bounded background pool, LLM-cost logging, and workspace budget enforcement."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import HTTPException

from app import billing
from app.config import settings
from app.datasets import current_workspace
from app.db import get_connection

# Bounded pool for best-effort background work (notification fan-out, review
# nudges) so a burst of events can't spawn unbounded daemon threads.
bg_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cb-bg")


def log_llm_call(endpoint: str, model, prompt_tokens, completion_tokens, cost_usd,
                 latency_ms, user_id=None) -> None:
    try:
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO llm_calls (endpoint, model, prompt_tokens,
                       completion_tokens, cost_usd, latency_ms, user_id, workspace_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (endpoint, model, prompt_tokens, completion_tokens, cost_usd, latency_ms,
                 user_id, current_workspace()),
            )
            conn.execute(
                """INSERT INTO ai_spend_daily (day, cost_usd) VALUES (?, ?)
                   ON CONFLICT (day) DO UPDATE
                   SET cost_usd = ai_spend_daily.cost_usd + EXCLUDED.cost_usd""",
                (_today_utc()[:10], cost_usd or 0),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception:
        pass  # cost logging must never fail a request


def _today_utc() -> str:
    # 'YYYY-MM-DD 00:00:00' compares correctly against TEXT (SQLite) and TIMESTAMPTZ (PG).
    return datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")


def _spent_today(conn, user_id: str | None = None) -> float:
    if user_id is None:
        row = conn.execute("SELECT cost_usd FROM ai_spend_daily WHERE day = ?", (_today_utc()[:10],)).fetchone()
        return float(row["cost_usd"]) if row else 0.0
    sql = "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_calls WHERE created_at >= ?"
    params: tuple = (_today_utc(),)
    if user_id is not None:
        sql += " AND user_id = ?"
        params += (user_id,)
    return float(conn.execute(sql, params).fetchone()["s"] or 0.0)


def enforce_daily_caps(conn, user) -> None:
    """Portfolio safety: anyone can sign in, so cap AI spend per visitor per UTC
    day and in total per day. Admins (the owner) are exempt."""
    if user is None or getattr(user, "role", None) == "admin":
        return
    per_user, overall = settings.user_daily_ai_budget_usd, settings.global_daily_ai_budget_usd
    if overall and _spent_today(conn) >= overall:
        raise HTTPException(
            status_code=429,
            detail="Today's AI allowance for this demo is used up. Drafting resets at midnight UTC; "
                   "everything else still works.")
    if per_user and _spent_today(conn, user.id) >= per_user:
        raise HTTPException(
            status_code=429,
            detail=f"You've used today's AI allowance (${per_user:.2f}). It resets at midnight UTC; "
                   "everything else still works.")


def enforce_budget(user=None) -> None:
    """Pre-flight spend limits before any LLM call: the visitor's and the overall
    daily caps, then the active workspace's monthly budget (no-op outside a
    workspace scope: local-dev/tests/demo)."""
    conn = get_connection()
    try:
        enforce_daily_caps(conn, user)
    finally:
        conn.close()
    ws = current_workspace()
    if ws is None:
        return
    conn = get_connection()
    try:
        if billing.is_over_budget(conn, ws):
            u = billing.usage(conn, ws)
            raise HTTPException(
                status_code=402,
                detail=(f"Workspace monthly LLM budget reached "
                        f"(${u['month_to_date_usd']:.2f} of ${u['monthly_budget_usd']:.2f}). "
                        f"Raise the limit in workspace settings to continue."),
            )
    finally:
        conn.close()
