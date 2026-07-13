"""Prompt templates for the narrative generator. Versioned so eval can compare
prompt versions over time (PRD Section 11).
"""

from app.schemas import (
    ComputedFact,
    ContextSnippet,
    CorrelationPair,
    PeriodComparison,
    TrendStreak,
)

PROMPT_VERSION = "v2"

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


# A concise variant for A/B testing against the baseline. Same hard rules
# (faithfulness is non-negotiable), tighter output.
SYSTEM_PROMPT_CONCISE = """You are a financial narrative writer for an FP&A team. \
State what a KPI did and why, for a busy executive.

Hard rules, no exceptions:
1. Use ONLY the numbers in the "Computed facts" block. Never invent, estimate, or \
re-round a number that is not present there.
2. Attribute a cause ONLY to a provided context snippet. If none explains the move, \
say it is unexplained this period.
3. Write exactly 1-2 tight sentences. Lead with the movement and its magnitude.
4. Return the narrative plus the ids of the context snippets you relied on (empty if none)."""


# Named prompt variants the A/B harness compares. Prompts are code (git-reviewed);
# promotion = changing which variant SYSTEM_PROMPT points at, via PR.
PROMPT_VARIANTS = {
    "baseline": SYSTEM_PROMPT,
    "concise": SYSTEM_PROMPT_CONCISE,
}


QA_SYSTEM_PROMPT = """You are a financial analyst answering a follow-up question \
about one KPI for an FP&A team.

Hard rules, no exceptions:
1. Use ONLY the numbers in the "Computed facts" block. Never invent, estimate, or \
extrapolate a number.
2. Ground causes ONLY in the provided context snippets. If neither the facts nor \
the context answer the question, say plainly: "The data available doesn't answer \
that" — do not guess.
3. Answer in 1-3 sentences of plain prose, directly addressing the question.
4. Return the answer plus the ids of any context snippets you relied on (empty \
list if none)."""


def build_qa_prompt(fact: ComputedFact, context: list[ContextSnippet], question: str,
                    history: list[dict] | None = None) -> str:
    """QA prompt for one metric. `history` is prior [{question, answer}] turns in
    this thread so follow-ups like "why?" or "and vs last year?" resolve against
    the conversation — the numbers still come only from the facts block."""
    base = build_user_prompt(fact, context)
    convo = ""
    if history:
        lines = ["", "Conversation so far (earlier turns in this thread):"]
        for turn in history[-6:]:   # cap: recent turns only, keeps tokens bounded
            q = str(turn.get("question", "")).strip()
            a = str(turn.get("answer", "")).strip()[:600]
            if q and a:
                lines.append(f'  Q: "{q}"')
                lines.append(f"  A: {a}")
        if len(lines) > 2:
            convo = "\n".join(lines)
    return (base + convo
            + f'\n\nNew question from the analyst: "{question.strip()}"'
            + "\nAnswer it under the rules above, using the conversation only to "
              "interpret what the question refers to (never as a source of numbers).")


FUNNEL_SYSTEM_PROMPT = """You are a growth analyst summarizing an acquisition funnel \
for a marketing leadership team.

Hard rules, no exceptions:
1. Use ONLY the numbers in the "Funnel" block. Never invent, estimate, or derive a number.
2. Lead with where the funnel leaks most (the lowest stage-over-stage conversion) and \
whether that conversion improved or worsened versus the prior period.
3. Write 2-4 sentences of plain, leadership-ready prose. No bullet points.
4. Return the narrative and an empty list of source ids (this summary is computed, not retrieved)."""


def build_funnel_prompt(funnel: dict) -> str:
    """Deterministic funnel facts (from app/compute/funnel) rendered for the LLM to
    phrase — the model never recomputes conversions."""
    lines = [f"Funnel for {funnel.get('period')}"]
    if funnel.get("prior_period"):
        lines.append(f"Prior period (for stage-over-stage change): {funnel['prior_period']}")
    lines.append("")
    lines.append("Funnel (the ONLY numbers you may use):")
    for s in funnel.get("stages", []):
        parts = [f"  - {s['name']}: {s['value']:,.0f}"]
        if s.get("conversion_from_prev") is not None:
            parts.append(f"{s['conversion_from_prev']}% conversion from previous stage")
        if s.get("conversion_mom_pp") is not None:
            parts.append(f"({s['conversion_mom_pp']:+.2f}pp vs prior period)")
        if s.get("drop_off") is not None:
            parts.append(f"drop-off {s['drop_off']:,.0f}")
        lines.append(", ".join(parts))
    if funnel.get("biggest_dropoff_stage"):
        lines.append(f"\nBiggest leak: conversion into {funnel['biggest_dropoff_stage']}.")
    if funnel.get("overall_conversion") is not None:
        lines.append(f"Overall conversion (last/first): {funnel['overall_conversion']}%.")
    lines.append("\nWrite the funnel summary under the rules above.")
    return "\n".join(lines)


FORECAST_SYSTEM_PROMPT = """You are an FP&A analyst writing a short forward-looking \
outlook for an executive.

Hard rules, no exceptions:
1. Use ONLY the numbers in the "Forecast" block (projected values, budget, MAPE). \
Never invent or recompute a number.
2. Say plainly that these are model projections, not actuals. Note the forecast \
error (MAPE) if given, and whether the projection is above or below budget.
3. Write 2-3 sentences of plain, executive-ready prose. No bullet points.
4. Return the narrative and an empty list of source ids (this outlook is computed)."""


def build_forecast_prompt(metric: str, unit: str, history_tail: list[dict],
                          projections: list[dict], mape: float | None) -> str:
    lines = [f"Metric: {metric}", "", "Recent actuals:"]
    for h in history_tail[-4:]:
        lines.append(f"  - {h['period']}: {_fmt(h['value'])}")
    lines.append("")
    lines.append("Forecast (the ONLY numbers you may use — model projections):")
    for p in projections:
        line = f"  - {p['period']}: projected {_fmt(p['value'])}"
        if p.get("budget") is not None:
            line += f" vs budget {_fmt(p['budget'])} ({'above' if p['value'] >= p['budget'] else 'below'} plan)"
        lines.append(line)
    if mape is not None:
        lines.append(f"\nBacktest error (MAPE): {mape}%")
    lines.append("\nWrite the outlook under the rules above.")
    return "\n".join(lines)


def _fmt(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:,.2f}{suffix}"


def build_correlation_context(correlations: list[CorrelationPair], metric: str) -> list[str]:
    if not correlations:
        return []
    lines = [
        "",
        "Correlated metrics (deterministic, historical — for context only, do NOT "
        "state these as the cause unless a context snippet supports it):",
    ]
    for c in correlations[:4]:
        rel = "moves with" if c.direction == "positive" else "moves opposite to"
        lines.append(
            f'  - "{metric}" {rel} "{c.metric_b}" ({c.strength.replace("_", " ")}, '
            f"r={c.r:+.2f} over {c.months} months)"
        )
    return lines


def build_trend_streak_context(streak: TrendStreak | None) -> list[str]:
    if streak is None:
        return []
    return [
        "",
        f"Trend streak (deterministic): this metric has been {streak.direction} for "
        f"{streak.months} consecutive months ({streak.start_period} → {streak.end_period}).",
    ]


def build_comparison_context(comparison: PeriodComparison | None) -> list[str]:
    if comparison is None:
        return []
    c = comparison
    line = (
        f"Compared to {c.period_a}, {c.period_b} changed by {_fmt(c.abs_change)} "
        f"({_fmt(c.pct_change, '%')})"
    )
    if c.momentum:
        line += f"; the month-over-month movement is {c.momentum}"
        if c.acceleration is not None:
            line += f" (MoM {_fmt(c.mom_pct_a, '%')} → {_fmt(c.mom_pct_b, '%')})"
    return ["", "Period comparison (deterministic):", "  - " + line + "."]


def build_user_prompt(
    fact: ComputedFact,
    context: list[ContextSnippet],
    correlations: list[CorrelationPair] | None = None,
    trend_streak: TrendStreak | None = None,
    comparison: PeriodComparison | None = None,
) -> str:
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
    lines += build_trend_streak_context(trend_streak)
    lines += build_comparison_context(comparison)
    lines += build_correlation_context(correlations or [], fact.metric)
    lines.append("")
    if context:
        lines.append("Retrieved context (cite by id if you use it):")
        for c in context:
            lines.append(f'  - id="{c.id}" type="{c.type}" title="{c.title}": {c.body}')
    else:
        lines.append("Retrieved context: none provided.")

    return "\n".join(lines)
