"""Prompt templates for the narrative generator. Versioned so eval can compare
prompt versions over time (PRD Section 11).
"""

from app.schemas import ComputedFact, ContextSnippet

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are a financial narrative writer for an FP&A team. \
You explain KPI movements to executives in plain English.

Hard rules, no exceptions:
1. Use ONLY the numbers given to you in the "Computed facts" block. Never invent, \
estimate, round differently, or introduce any number not explicitly present there.
2. Attribute causes ONLY to the provided context snippets. If no context snippet \
plausibly explains the movement, say the movement is unexplained this period — do \
not guess a cause.
3. Write 2-4 sentences of plain, executive-ready prose. No bullet points, no headers.
4. Return your answer as the narrative plus the list of context snippet ids you \
actually relied on (empty list if none were used or none were relevant)."""


def _fmt(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:,.2f}{suffix}"


def build_user_prompt(fact: ComputedFact, context: list[ContextSnippet]) -> str:
    lines = [
        f"Metric: {fact.metric}",
        f"Period: {fact.period}",
        "Computed facts (the ONLY numbers you may use):",
        f"  - value: {_fmt(fact.value)} {fact.unit}",
        f"  - prior_period_value: {_fmt(fact.prior_value)} {fact.unit}",
        f"  - month_over_month_change: {_fmt(fact.deltas.mom_pct, '%')}",
        f"  - year_over_year_change: {_fmt(fact.deltas.yoy_pct, '%')}",
        f"  - budget_variance_absolute: {_fmt(fact.deltas.budget_var_abs)} {fact.unit}",
        f"  - budget_variance_percent: {_fmt(fact.deltas.budget_var_pct, '%')}",
        f"  - trend_12mo: {fact.trend or 'n/a'}",
        f"  - is_anomaly: {fact.is_anomaly}",
    ]
    if fact.variance_bridge:
        lines.append("  - variance decomposition (deterministic):")
        for component, impact in fact.variance_bridge.items():
            lines.append(f"      - {component}_effect: {_fmt(impact)} {fact.unit}")
    else:
        lines.append(
            "  - variance decomposition: not available — do NOT attribute the "
            "movement to price, volume, or mix effects."
        )
    lines.append("")
    if context:
        lines.append("Retrieved context (cite by id if you use it):")
        for c in context:
            lines.append(f'  - id="{c.id}" type="{c.type}" title="{c.title}": {c.body}')
    else:
        lines.append("Retrieved context: none provided.")

    return "\n".join(lines)
