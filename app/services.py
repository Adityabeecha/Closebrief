"""Cross-cutting request helpers shared by main and the feature routers: the
bounded background pool, LLM-cost logging, and workspace budget enforcement."""

from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

from app import billing
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
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # cost logging must never fail a request


def enforce_budget() -> None:
    """Pre-flight spend limit: block LLM work when the active workspace is over its
    monthly budget. No-op outside a workspace scope (local-dev/tests/demo)."""
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
