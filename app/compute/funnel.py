"""Funnel analysis (Phase 1): stage-over-stage conversion for an ordered
sequence of metrics (e.g. Impressions -> Clicks -> Signups -> Conversions).

Deterministic, like the rest of app/compute — the LLM never computes these
numbers, it only phrases them (see app/generation/prompts.build_funnel_prompt).
The funnel *order* comes from the active dataset's domain (DomainConfig.funnel);
the values are ordinary computed_facts, read here and interpreted as a funnel.
"""

from __future__ import annotations

import sqlite3


def _value(conn: sqlite3.Connection, dataset_id: int, metric: str, period: str) -> float | None:
    row = conn.execute(
        """SELECT cf.value FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
           WHERE m.dataset_id = ? AND m.name = ? AND cf.period = ?""",
        (dataset_id, metric, period),
    ).fetchone()
    return float(row["value"]) if row and row["value"] is not None else None


def _prior_period(conn: sqlite3.Connection, dataset_id: int, period: str) -> str | None:
    row = conn.execute(
        """SELECT DISTINCT cf.period FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
           WHERE m.dataset_id = ? AND cf.period < ? ORDER BY cf.period DESC LIMIT 1""",
        (dataset_id, period),
    ).fetchone()
    return row["period"] if row else None


def _conversions(conn, dataset_id: int, period: str, stages: list[str]) -> dict[str, float]:
    """conversion% from the previous *present* stage, keyed by stage name."""
    present = [(s, _value(conn, dataset_id, s, period)) for s in stages]
    present = [(s, v) for s, v in present if v is not None]
    out: dict[str, float] = {}
    for i in range(1, len(present)):
        prev_v = present[i - 1][1]
        if prev_v:
            out[present[i][0]] = round(present[i][1] / prev_v * 100, 2)
    return out


def compute_funnel(conn: sqlite3.Connection, dataset_id: int, period: str,
                   stages: list[str]) -> dict:
    """Return the ordered funnel for `period`: each present stage with its value,
    conversion-from-previous-stage %, absolute drop-off, and the MoM change in
    that conversion rate. Also the biggest drop-off stage and overall conversion.
    """
    prior = _prior_period(conn, dataset_id, period)
    prior_conv = _conversions(conn, dataset_id, prior, stages) if prior else {}

    rows = [(s, _value(conn, dataset_id, s, period)) for s in stages]
    present = [(s, v) for s, v in rows if v is not None]

    out_stages = []
    prev_val: float | None = None
    for name, value in present:
        conv = drop = conv_mom = None
        if prev_val is not None and prev_val:
            conv = round(value / prev_val * 100, 2)
            drop = round(prev_val - value, 2)
            if name in prior_conv and prior_conv[name] is not None:
                conv_mom = round(conv - prior_conv[name], 2)   # pp change in rate
        out_stages.append({
            "name": name,
            "value": value,
            "conversion_from_prev": conv,
            "drop_off": drop,
            "conversion_mom_pp": conv_mom,
        })
        prev_val = value

    # Biggest drop-off = the transition with the lowest conversion rate.
    with_conv = [s for s in out_stages if s["conversion_from_prev"] is not None]
    biggest = min(with_conv, key=lambda s: s["conversion_from_prev"])["name"] if with_conv else None
    overall = None
    if len(present) >= 2 and present[0][1]:
        overall = round(present[-1][1] / present[0][1] * 100, 2)

    return {
        "period": period,
        "prior_period": prior,
        "stages": out_stages,
        "biggest_dropoff_stage": biggest,
        "overall_conversion": overall,
    }
