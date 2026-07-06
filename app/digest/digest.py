"""Executive digest (US-C2): top-N movements for a period ranked by absolute
budget variance, each as a one-line headline + one explanatory sentence.
Ranking and number selection are deterministic; the LLM only phrases prose,
and each item is checked by the faithfulness guard against its own facts.
"""

import json
import sqlite3
import time

from pydantic import BaseModel

from app.generation.guard import check_faithfulness
from app.generation.llm_client import LLMClient, TokenUsage
from app.generation.prompts import PROMPT_VERSION
from app.schemas import ComputedFact, Deltas


class DigestItem(BaseModel):
    metric: str
    headline: str
    detail: str
    budget_var_abs: float | None = None
    budget_var_pct: float | None = None
    faithfulness: str = "unchecked"
    # Persisted like any narrative so it is auditable and feedback-able.
    report_id: int | None = None


class DigestOutput(BaseModel):
    period: str
    items: list[DigestItem]
    cached: bool = False
    cost_usd: float | None = None


DIGEST_SYSTEM_PROMPT = """You are a financial narrative writer producing an executive digest.
For the single metric provided, respond with EXACTLY two lines:
Line 1 — headline: one plain-English line stating the movement (may include the provided numbers).
Line 2 — detail: exactly one sentence explaining the scale of the move vs budget.

Separate the two lines with a single newline character.
Use ONLY the numbers provided. Never invent or derive new figures."""


def _load_facts_for_period(conn: sqlite3.Connection, period: str) -> list[ComputedFact]:
    from app.datasets import active_dataset_id

    ds = active_dataset_id(conn)
    rows = conn.execute(
        """
        SELECT m.name AS metric, m.category, m.unit, cf.period, cf.value, cf.prior_value,
               cf.mom_pct, cf.yoy_pct, cf.budget_var_abs, cf.budget_var_pct, cf.trend, cf.is_anomaly
        FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
        WHERE cf.period = ? AND m.dataset_id = ?
        """,
        (period, ds),
    ).fetchall()
    return [
        ComputedFact(
            metric=r["metric"],
            category=r["category"],
            period=r["period"],
            value=r["value"],
            unit=r["unit"],
            prior_value=r["prior_value"],
            deltas=Deltas(
                mom_pct=r["mom_pct"],
                yoy_pct=r["yoy_pct"],
                budget_var_abs=r["budget_var_abs"],
                budget_var_pct=r["budget_var_pct"],
            ),
            trend=r["trend"],
            is_anomaly=bool(r["is_anomaly"]),
        )
        for r in rows
    ]


def generate_digest(
    conn: sqlite3.Connection, period: str, llm_client: LLMClient, top_n: int = 5
) -> DigestOutput:
    facts = _load_facts_for_period(conn, period)
    ranked = sorted(
        (f for f in facts if f.deltas.budget_var_abs is not None),
        key=lambda f: abs(f.deltas.budget_var_abs),
        reverse=True,
    )[:top_n]

    items: list[DigestItem] = []
    total_cost = 0.0
    for fact in ranked:
        user_prompt = (
            f"Metric: {fact.metric}\nPeriod: {fact.period}\n"
            f"value: {fact.value:,.2f} {fact.unit}\n"
            f"mom_pct: {fact.deltas.mom_pct}\n"
            f"budget_var_abs: {fact.deltas.budget_var_abs:,.2f} {fact.unit}\n"
            f"budget_var_pct: {fact.deltas.budget_var_pct}\n"
        )
        t0 = time.perf_counter()
        result, usage = llm_client.generate_narrative(DIGEST_SYSTEM_PROMPT, user_prompt)
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        total_cost += usage.cost_usd or 0.0
        text = result.narrative
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        headline = lines[0] if lines else text
        detail = " ".join(lines[1:]) if len(lines) > 1 else ""
        passed, _ = check_faithfulness(text, fact)
        report_id = _persist_digest_item(conn, fact, text, passed, usage, latency_ms)
        items.append(
            DigestItem(
                metric=fact.metric,
                headline=headline,
                detail=detail,
                budget_var_abs=fact.deltas.budget_var_abs,
                budget_var_pct=fact.deltas.budget_var_pct,
                faithfulness="passed" if passed else "failed",
                report_id=report_id,
            )
        )
    return DigestOutput(
        period=period, items=items, cost_usd=round(total_cost, 6) if items else None
    )


def _persist_digest_item(
    conn: sqlite3.Connection,
    fact: ComputedFact,
    text: str,
    passed: bool,
    usage: TokenUsage,
    latency_ms: float,
) -> int | None:
    """Digest lines are narratives too: persist them with full telemetry so
    they are auditable and can receive feedback."""
    from app.datasets import active_dataset_id

    ds = active_dataset_id(conn)
    mrow = conn.execute(
        "SELECT id FROM metrics WHERE name = ? AND dataset_id = ?", (fact.metric, ds)
    ).fetchone()
    if mrow is None:
        return None
    cur = conn.execute(
        """
        INSERT INTO generated_reports (metric_id, period, narrative, sources,
                    confidence, faithfulness, prompt_tokens, completion_tokens,
                    cost_usd, latency_ms, prompt_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(mrow["id"]),
            fact.period,
            text,
            json.dumps([]),
            "Medium",
            "passed" if passed else "failed",
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.cost_usd,
            latency_ms,
            f"{PROMPT_VERSION}-digest",
        ),
    )
    conn.commit()
    return int(cur.lastrowid) if cur.lastrowid else None
