"""Executive digest (US-C2), built only from approved commentary.

The digest is what leadership reads, so it never contains text a person hasn't
approved: each item is the latest approved commentary for one of the close's
biggest movers (ranked by absolute variance against plan, then by the change
since last period). Building it is deterministic and free; sending it is a
separate, explicit step that is refused until every KPI in the close is
approved (see `ready`).
"""

import re
import sqlite3

from pydantic import BaseModel, Field

from app.compute.availability import has_usable_value


class DigestItem(BaseModel):
    metric: str
    headline: str
    detail: str
    budget_var_abs: float | None = None
    budget_var_pct: float | None = None
    unit: str | None = None            # how the variance is written (points for "%")
    direction_good: str | None = None  # which way is good, for the colour
    faithfulness: str = "unchecked"
    report_id: int | None = None


class DigestOutput(BaseModel):
    period: str
    items: list[DigestItem]
    cached: bool = False
    cost_usd: float | None = None
    total_kpis: int = 0
    approved_kpis: int = 0
    pending: list[str] = Field(default_factory=list)   # KPIs still waiting for approval
    ready: bool = False                                 # every KPI approved: may be sent
    sent_at: str | None = None


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9$\u2212+-])")


def _split_headline(narrative: str) -> tuple[str, str]:
    """First sentence as the headline, the rest as detail."""
    text = " ".join((narrative or "").split())
    parts = _SENTENCE_END.split(text, maxsplit=1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def build_approved_digest(conn: sqlite3.Connection, period: str,
                          dataset_id: int | None = None, top_n: int = 5) -> DigestOutput:
    """The digest for one close from approved commentary only. Counts the same
    KPI set as the close checklist: KPIs with a value this period, limited to
    the dataset's selected KPIs when a selection exists."""
    from app.datasets import active_dataset_id

    ds = dataset_id if dataset_id is not None else active_dataset_id(conn)
    if ds is None:
        return DigestOutput(period=period, items=[])
    rows = conn.execute(
        """
        SELECT m.name AS metric, cf.budget_var_abs, cf.budget_var_pct, cf.mom_pct,
               gr.id AS report_id, gr.narrative, gr.review_status, gr.faithfulness,
               m.id AS metric_id, cf.value, mv.source_rows,
               COALESCE(kc.unit, m.unit) AS unit, COALESCE(kc.direction_good, m.direction_good) AS direction_good
        FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
        LEFT JOIN kpi_configs kc ON kc.dataset_id = m.dataset_id AND kc.source_metric = m.name
        LEFT JOIN metric_values mv ON mv.metric_id = m.id AND mv.period = cf.period
        LEFT JOIN generated_reports gr ON gr.id = (
            SELECT MAX(g.id) FROM generated_reports g
            WHERE g.metric_id = cf.metric_id AND g.period = cf.period
              AND (g.prompt_version IS NULL OR g.prompt_version NOT LIKE '%-digest'))
        WHERE m.dataset_id = ? AND cf.period = ? AND cf.value IS NOT NULL
          AND (NOT EXISTS (SELECT 1 FROM kpi_configs k WHERE k.dataset_id = m.dataset_id)
               OR EXISTS (SELECT 1 FROM kpi_configs k
                          WHERE k.dataset_id = m.dataset_id AND k.source_metric = m.name))
        ORDER BY m.name
        """,
        (ds, period),
    ).fetchall()
    rows = [r for r in rows if has_usable_value(r["metric_id"], r["value"], r["unit"], r["source_rows"])]
    approved = [r for r in rows if r["review_status"] == "approved" and (r["narrative"] or "").strip()]
    pending = [r["metric"] for r in rows if r not in approved]
    ranked = sorted(
        approved,
        key=lambda r: (abs(r["budget_var_abs"]) if r["budget_var_abs"] is not None else -1,
                       abs(r["mom_pct"]) if r["mom_pct"] is not None else -1),
        reverse=True,
    )[:top_n]
    items = []
    for r in ranked:
        headline, detail = _split_headline(r["narrative"])
        items.append(DigestItem(
            metric=r["metric"], headline=headline, detail=detail,
            budget_var_abs=r["budget_var_abs"], budget_var_pct=r["budget_var_pct"],
            unit=r["unit"], direction_good=r["direction_good"],
            faithfulness=r["faithfulness"] or "unchecked", report_id=r["report_id"],
        ))
    return DigestOutput(
        period=period, items=items, total_kpis=len(rows), approved_kpis=len(approved),
        pending=pending, ready=bool(rows) and not pending,
    )


def last_sent_at(conn: sqlite3.Connection, dataset_id: int | None, period: str) -> str | None:
    """When this close's digest last went out (manual send or schedule)."""
    if dataset_id is None:
        return None
    row = conn.execute(
        "SELECT MAX(created_at) AS t FROM digest_runs WHERE dataset_id = ? AND period = ?",
        (dataset_id, period),
    ).fetchone()
    return str(row["t"]) if row and row["t"] else None
