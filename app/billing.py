"""Usage metering & spend limits (v4.0). Per-workspace LLM cost tracking with a
pre-flight budget check. Free-tier scope: metering + enforcement; invoicing
(Stripe) is out. Default budgets come from the workspace's plan/tier.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Tier → default monthly LLM budget (USD). A workspace's explicit
# monthly_budget_usd overrides its tier default; None on both = unlimited.
TIER_BUDGETS = {"free": 5.0, "starter": 50.0, "pro": 250.0, "enterprise": None}


def _month_start() -> str:
    now = datetime.now(timezone.utc)
    # 'YYYY-MM-01' compares correctly against TEXT (SQLite) and TIMESTAMPTZ (PG).
    return f"{now.year:04d}-{now.month:02d}-01"


def month_to_date_spend(conn, workspace_id: int) -> float:
    row = conn.execute(
        """SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_calls
           WHERE workspace_id = ? AND created_at >= ?""",
        (workspace_id, _month_start()),
    ).fetchone()
    return float(row["s"] or 0.0)


def effective_budget(conn, workspace_id: int) -> float | None:
    """The workspace's monthly budget: explicit override, else its tier default,
    else None (unlimited)."""
    row = conn.execute(
        "SELECT plan, monthly_budget_usd FROM workspaces WHERE id = ?", (workspace_id,)
    ).fetchone()
    if row is None:
        return None
    if row["monthly_budget_usd"] is not None:
        return float(row["monthly_budget_usd"])
    return TIER_BUDGETS.get(row["plan"] or "free")


def is_over_budget(conn, workspace_id: int) -> bool:
    budget = effective_budget(conn, workspace_id)
    if budget is None:
        return False
    return month_to_date_spend(conn, workspace_id) >= budget


def usage(conn, workspace_id: int) -> dict:
    row = conn.execute(
        "SELECT plan, monthly_budget_usd FROM workspaces WHERE id = ?", (workspace_id,)
    ).fetchone()
    spend = month_to_date_spend(conn, workspace_id)
    budget = effective_budget(conn, workspace_id)
    return {
        "plan": (row["plan"] if row else "free"),
        "month_to_date_usd": round(spend, 6),
        "monthly_budget_usd": budget,
        "remaining_usd": (round(budget - spend, 6) if budget is not None else None),
        "over_budget": (budget is not None and spend >= budget),
    }
