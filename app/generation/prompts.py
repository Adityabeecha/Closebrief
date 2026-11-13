"""Prompt templates for the narrative generator. Versioned so eval can compare
prompt versions over time (PRD Section 11).
"""

import math
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.currency import symbol
from app.schemas import (
    ComputedFact,
    ContextSnippet,
    CorrelationPair,
    PeriodComparison,
    TrendStreak,
)

PROMPT_VERSION = "v6-close-commentary"

FIGURE_RULES = """Copy figures exactly as supplied, including currency symbols, signs, suffixes and precision. Never calculate or introduce numbers, \
including numbers from context notes that are absent from Computed facts."""

SYSTEM_PROMPT = """You write month-end KPI commentary for an FP&A team in plain English.
Use fewer than 90 words, normally three short sentences, with no headings, bullets or hype.
Open with the KPI's value and how it moved versus plan, using the supplied budget variance; if no budget comparison exists, \
use the supplied prior-period movement.
When contributors are supplied, say which part drove the variance, using its supplied figures.
Then give the reason ONLY when a relevant analyst context note supports it. State the reason itself in plain words, as a finance \
analyst would; never mention notes, note titles, "context" or sources in the prose. Return the note's id in sources_used. If no \
note explains the move, say nothing about a reason. Never invent a cause.
For down-is-good KPIs a rise is unfavourable; never call a rising cost strong or positive.
Never use generic "watch for continued growth" filler; ground watch items in a note or the trend.
End with what to watch next month ONLY when an available trend, anomaly or recorded context supports a specific observation. Do not \
forecast or invent a target; omit this sentence when unsupported.
Every number must come from Computed facts. Return the narrative and only the context ids actually used.""" \
    + "\n\n" + FIGURE_RULES

SYSTEM_PROMPT_CONCISE = SYSTEM_PROMPT


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
2. Ground causes ONLY in the provided context snippets. When a snippet is about \
this metric and period, answer from it directly: name the event, and \
you may repeat figures written in that snippet. Say plainly "The data available \
doesn't answer that" ONLY when neither the facts nor any snippet relate to the \
question; never add it as a hedge to an answer you have given. Do not guess.
Never mention snippet ids or internal ctx_ identifiers in the answer; use sources_used for ids.
3. Answer in 1-3 sentences of plain prose, directly addressing the question.
4. Return the answer plus the ids of any context snippets you relied on (empty \
list if none)."""


QA_SYSTEM_PROMPT += "\n\nCopy all figures exactly as given; do not expand compact amounts or add precision."


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


FUNNEL_SYSTEM_PROMPT += "\n\nCopy all figures exactly as given; do not expand compact amounts or add precision."


def build_funnel_prompt(funnel: dict) -> str:
    """Deterministic funnel facts (from app/compute/funnel) rendered for the LLM to
    phrase — the model never recomputes conversions."""
    lines = [f"Funnel for {funnel.get('period')}"]
    if funnel.get("prior_period"):
        lines.append(f"Prior period (for stage-over-stage change): {funnel['prior_period']}")
    lines.append("")
    lines.append("Funnel (the ONLY numbers you may use):")
    for s in funnel.get("stages", []):
        parts = [f"  - {s['name']}: {_fmt(s['value'], 'count')}"]
        if s.get("conversion_from_prev") is not None:
            parts.append(f"{_fmt(s['conversion_from_prev'], '%')} conversion from previous stage")
        if s.get("conversion_mom_pp") is not None:
            parts.append(f"({_fmt(s['conversion_mom_pp'], '%')[:-1]}pp vs prior period)")
        if s.get("drop_off") is not None:
            parts.append(f"drop-off {_fmt(s['drop_off'], 'count')}")
        lines.append(", ".join(parts))
    if funnel.get("biggest_dropoff_stage"):
        lines.append(f"\nBiggest leak: conversion into {funnel['biggest_dropoff_stage']}.")
    if funnel.get("overall_conversion") is not None:
        lines.append(f"Overall conversion (last/first): {_fmt(funnel['overall_conversion'], '%')}.")
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


FORECAST_SYSTEM_PROMPT += "\n\nCopy all figures exactly as given; do not expand compact amounts or add precision."


def build_forecast_prompt(metric: str, unit: str, history_tail: list[dict],
                          projections: list[dict], mape: float | None, currency: str = "USD") -> str:
    lines = [f"Metric: {metric}", "", "Recent actuals:"]
    for h in history_tail[-4:]:
        lines.append(f"  - {h['period']}: {_fmt(h['value'], unit, currency)}")
    lines.append("")
    lines.append("Forecast (the ONLY numbers you may use — model projections):")
    for p in projections:
        line = f"  - {p['period']}: projected {_fmt(p['value'], unit, currency)}"
        if p.get("budget") is not None:
            line += f" vs budget {_fmt(p['budget'], unit, currency)} ({'above' if p['value'] >= p['budget'] else 'below'} plan)"
        lines.append(line)
    if mape is not None:
        lines.append(f"\nBacktest error (MAPE): {_fmt(mape, '%')}")
    lines.append("\nWrite the outlook under the rules above.")
    return "\n".join(lines)


ROOT_CAUSE_SYSTEM_PROMPT = """You are an FP&A analyst explaining WHY a metric \
moved this period, for an executive.

Hard rules, no exceptions:
1. Use ONLY the numbers in the "Decomposition" block (the variance, the \
price/volume/mix split, correlated movers, the baseline/z-score). Never invent \
or recompute a number.
2. Lead with the single biggest driver. When a price/volume/mix split is given, \
attribute the move concretely (how much came from each).
3. If a z-score is given, say plainly how unusual this is versus the metric's own \
recent baseline. Treat correlated movers as context, not proven causation.
4. Write 2-4 sentences of plain, executive-ready prose. No bullet points.
5. Return the narrative and an empty list of source ids (this explanation is computed)."""


ROOT_CAUSE_SYSTEM_PROMPT += "\n\nCopy all figures exactly as given; do not expand compact amounts or add precision."


def build_root_cause_prompt(rc: dict) -> str:
    unit = rc.get("unit", "USD")
    cur = rc.get("currency") or "USD"
    lines = [f"Metric: {rc['metric']} ({rc['period']})", f"Value: {_fmt(rc.get('value'), unit, cur)}"]
    if rc.get("prior_value") is not None:
        lines.append(f"Prior period: {_fmt(rc['prior_value'], unit, cur)}")
    if rc.get("mom_pct") is not None:
        lines.append(f"Month-over-month: {_fmt(rc['mom_pct'], '%')}")
    if rc.get("budget_var_pct") is not None:
        lines.append(f"Vs plan: {_fmt(rc['budget_var_pct'], '%')} ({_fmt(rc.get('budget_var_abs'), _delta_unit(unit), cur)})")
    if rc.get("z_score") is not None:
        lines.append(
            f"Baseline: mean {_fmt(rc.get('baseline_mean'), unit, cur)}; this period is "
            f"{rc['z_score']} std devs from it"
            + (" (flagged as an anomaly)" if rc.get("is_anomaly") else ""))
    lines += ["", "Decomposition (the ONLY numbers you may use):"]
    if rc.get("pvm"):
        lines.append("  Price/Volume/Mix split of the variance:")
        for c in rc["pvm"]:
            lines.append(f"    - {c['component']}: {_fmt(c['impact'], _delta_unit(unit), cur)} ({_fmt(c['share_pct'], '%')} of the swing)")
    if rc.get("trend"):
        t = rc["trend"]
        lines.append(f"  Trend: {t.get('months')} consecutive months {t.get('direction')}")
    if rc.get("drivers"):
        lines.append("  Correlated movers (context only, not causation):")
        for d in rc["drivers"]:
            lines.append(f"    - {d['metric']} (r={d['r']}, {d['direction']})")
    lines.append(f"\nBiggest single factor: {rc.get('primary_factor')}")
    lines.append("\nExplain why the metric moved, under the rules above.")
    return "\n".join(lines)


def _fixed(value: float, places: int) -> str:
    """Match JavaScript toFixed: half ties away from zero, using the input float."""
    with localcontext() as ctx:
        ctx.prec = max(28, len(str(int(abs(value)))) + places + 2)
        rounded = Decimal.from_float(float(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{rounded:.{places}f}"


def _fmt(value: float | None, unit: str = "USD", currency: str = "USD") -> str:
    """Card-style amounts, signed percentages and whole counts for model input,
    with amounts in the dataset's currency."""
    if value is None or not math.isfinite(value):
        return "n/a"
    if unit.lower() == "count":
        count = math.floor(value + 0.5)   # JavaScript Math.round also rounds negative ties upwards.
        return ("\u2212" if count < 0 else "") + f"{abs(count):,}"
    if unit.lower() in ("number", "days"):
        places = 1 if unit.lower() == "days" else 2
        text = _fixed(abs(value), places).rstrip("0").rstrip(".")
        return ("\u2212" if value < 0 else "") + text + (" days" if unit.lower() == "days" else "")
    sign = "\u2212" if value < 0 else ""
    amount = abs(value)
    if unit.lower() in ("%", "percent", "pp"):
        suffix = "pp" if unit.lower() == "pp" else "%"
        return (sign or ("+" if value > 0 else "")) + _fixed(amount, 1) + suffix
    mark = symbol(currency)
    if amount >= 1e6:
        return sign + mark + _fixed(amount / 1e6, 2) + "M"
    if amount >= 1e5:
        return sign + mark + _fixed(amount / 1e3, 0) + "K"
    if amount >= 1e3:
        # In full under 100K, as on screen, so the commentary quotes what people see.
        return sign + mark + f"{int(_fixed(amount, 0)):,}"
    return sign + mark + _fixed(amount, 0 if currency == "JPY" else 2)


def _delta_unit(unit: str) -> str:
    """An absolute difference between rates is in percentage points, not percent."""
    return "pp" if unit.lower() in ("%", "percent") else unit


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


def build_comparison_context(comparison: PeriodComparison | None, unit: str = "USD",
                             currency: str = "USD") -> list[str]:
    if comparison is None:
        return []
    c = comparison
    line = (
        f"Compared to {c.period_a}, {c.period_b} changed by {_fmt(c.abs_change, _delta_unit(unit), currency)} "
        f"({_fmt(c.pct_change, '%')})"
    )
    if c.momentum:
        line += f"; the month-over-month movement is {c.momentum}"
        if c.acceleration is not None:
            line += f" (MoM {_fmt(c.mom_pct_a, '%')} → {_fmt(c.mom_pct_b, '%')})"
    return ["", "Period comparison (deterministic):", "  - " + line + "."]


def _is_pct_kpi(fact: ComputedFact) -> bool:
    """Rates and day counts change by absolute amounts (points, days), not by a percent of themselves."""
    return (fact.unit or "").lower() in ("%", "percent", "days")


def percent_kpi_points(fact: ComputedFact) -> dict[str, float | None]:
    """A percent KPI's changes in percentage points (38% vs a 40% plan is
    -2.0pp). The relative change of a rate (-5%) misleads finance readers."""
    v, prior, d = fact.value, fact.prior_value, fact.deltas
    yoy = None if v is None or d.yoy_pct is None or d.yoy_pct == -100 else v - v / (1 + d.yoy_pct / 100)
    return {"mom": None if v is None or prior is None else v - prior, "yoy": yoy, "budget": d.budget_var_abs}


def _point_changes(fact: ComputedFact) -> list[str]:
    pts = percent_kpi_points(fact)
    if (fact.unit or "").lower() == "days":
        def days(v):
            return "n/a" if v is None else ("+" if v > 0 else "") + _fmt(v, "days")
        return [
            "  - (this KPI is measured in days: its changes are in days; write them as days, never as a percent change)",
            f"  - month_over_month_change: {days(pts['mom'])}",
            f"  - year_over_year_change: {days(pts['yoy'])}",
            f"  - budget_variance: {days(pts['budget'])}",
        ]
    return [
        "  - (this KPI is itself a percentage: its changes are in percentage points, pp; "
        "write them as points, never as a percent change)",
        f"  - month_over_month_change: {_fmt(pts['mom'], 'pp')}",
        f"  - year_over_year_change: {_fmt(pts['yoy'], 'pp')}",
        f"  - budget_variance: {_fmt(pts['budget'], 'pp')}",
    ]


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
        f"  - value: {_fmt(fact.value, fact.unit, fact.currency)}",
        f"  - prior_period_value: {_fmt(fact.prior_value, fact.unit, fact.currency)}",
        *(_point_changes(fact) if _is_pct_kpi(fact) else [
            f"  - month_over_month_change: {_fmt(fact.deltas.mom_pct, '%')}",
            f"  - year_over_year_change: {_fmt(fact.deltas.yoy_pct, '%')}",
            f"  - budget_variance_absolute: {_fmt(fact.deltas.budget_var_abs, _delta_unit(fact.unit), fact.currency)}",
            f"  - budget_variance_percent: {_fmt(fact.deltas.budget_var_pct, '%')}",
        ]),
        f"  - trend_12mo: {fact.trend or 'n/a'}",
        f"  - is_anomaly: {fact.is_anomaly}",
    ]
    if fact.variance_bridge:
        lines.append("  - variance decomposition (deterministic):")
        for component, impact in fact.variance_bridge.items():
            lines.append(f"      - {component}_effect: {_fmt(impact, _delta_unit(fact.unit), fact.currency)}")
    else:
        lines.append(
            "  - variance decomposition: not available — do NOT size or split the "
            "movement into price, volume, or mix effects yourself. A cause stated in "
            "a context snippet may still be cited as the reason."
        )
    if fact.contributors:
        lines.append(f"  - contributors by {fact.contributor_dimension or 'part'} (largest variance first):")
        for c in fact.contributors:
            lines.append(
                f"      - {c['member']}: value {_fmt(c.get('value'), fact.unit, fact.currency)}, "
                f"plan {_fmt(c.get('budget'), fact.unit, fact.currency)}, "
                f"variance {_fmt(c.get('var_abs'), _delta_unit(fact.unit), fact.currency)} "
                f"({_fmt(c.get('var_pct'), '%')})")
    lines += build_trend_streak_context(trend_streak)
    lines += build_comparison_context(comparison, fact.unit, fact.currency)
    lines += build_correlation_context(correlations or [], fact.metric)
    lines.append("")
    if context:
        lines.append("Retrieved context (cite by id if you use it):")
        for c in context:
            lines.append(f'  - id="{c.id}" type="{c.type}" title="{c.title}": {c.body}')
    else:
        lines.append("Retrieved context: none provided.")

    return "\n".join(lines)
