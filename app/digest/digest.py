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


def _load_facts_for_period(conn: sqlite3.Connection, period: str,
                           dataset_id: int | None = None) -> list[ComputedFact]:
    from app.datasets import active_dataset_id

    ds = dataset_id if dataset_id is not None else active_dataset_id(conn)
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


def _fmt_amount(value: float | None, unit: str | None) -> str:
    """Executive-readable magnitude: $271.3K / $2.59M for USD, 1,234 otherwise.
    The guard's 2% tolerance verifies these against the raw fact."""
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if (unit or "").upper() == "USD":
        if v >= 1e9:
            return f"{sign}${v / 1e9:.2f}B"
        if v >= 1e6:
            return f"{sign}${v / 1e6:.2f}M"
        if v >= 1e4:
            return f"{sign}${v / 1e3:.1f}K"
        return f"{sign}${v:,.0f}"
    if (unit or "") == "%":
        return f"{value:.1f}%"
    return f"{sign}{v:,.0f}" if v >= 100 else f"{sign}{v:,.2f}".rstrip("0").rstrip(".")


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def build_digest_prompt(fact: ComputedFact) -> str:
    """Numbers are pre-rounded here: the system prompt forbids re-rounding, so
    raw floats (1.1999997910746822) would otherwise be copied verbatim."""
    return (
        f"Metric: {fact.metric}\nPeriod: {fact.period}\n"
        f"value: {_fmt_amount(fact.value, fact.unit)}\n"
        f"month_over_month: {_fmt_pct(fact.deltas.mom_pct)}\n"
        f"budget_variance: {_fmt_amount(fact.deltas.budget_var_abs, fact.unit)}\n"
        f"budget_variance_pct: {_fmt_pct(fact.deltas.budget_var_pct)}\n"
        "Quote these figures exactly as written.\n"
    )


def generate_digest(
    conn: sqlite3.Connection, period: str, llm_client: LLMClient, top_n: int = 5,
    dataset_id: int | None = None,
) -> DigestOutput:
    facts = _load_facts_for_period(conn, period, dataset_id=dataset_id)
    ranked = sorted(
        (f for f in facts if f.deltas.budget_var_abs is not None),
        key=lambda f: abs(f.deltas.budget_var_abs),
        reverse=True,
    )[:top_n]

    items: list[DigestItem] = []
    total_cost = 0.0
    for fact in ranked:
        user_prompt = build_digest_prompt(fact)
        t0 = time.perf_counter()
        result, usage = llm_client.generate_narrative(DIGEST_SYSTEM_PROMPT, user_prompt)
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        total_cost += usage.cost_usd or 0.0
        text = result.narrative
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        headline = lines[0] if lines else text
        detail = " ".join(lines[1:]) if len(lines) > 1 else ""
        passed, _ = check_faithfulness(text, fact)
        report_id = _persist_digest_item(conn, fact, text, passed, usage, latency_ms, dataset_id)
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
    dataset_id: int | None = None,
) -> int | None:
    """Digest lines are narratives too: persist them with full telemetry so
    they are auditable and can receive feedback."""
    from app.datasets import active_dataset_id

    ds = dataset_id if dataset_id is not None else active_dataset_id(conn)
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
