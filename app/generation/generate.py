"""facts + context -> narrative. Orchestrates prompt build, LLM call, and the
faithfulness guard. Never lets an unverified number leave the system."""

import re
import time

from app.compute.availability import DEFAULT_REASON_THRESHOLD, needs_reason
from app.context.reasons import dataset_notes, reason_recorded
from app.datasets import active_dataset_id
from app.db import get_connection
from app.generation.guard import check_faithfulness, faithfulness_messages
from app.generation.llm_client import LLMClient, LLMGenerationError
from app.generation.prompts import (
    FIGURE_RULES,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    _fmt,
    build_user_prompt,
    percent_kpi_points,
)
from app.schemas import ComputedFact, ContextSnippet, InsightOutput, SourceRef, TrendStreak


class GenerationFailedFactsOnly(Exception):
    """Raised when the LLM is unreachable. Carries the computed facts so the
    caller can still render a facts-only card (PRD US-C1 AC)."""

    def __init__(self, facts_only: InsightOutput):
        super().__init__("LLM generation failed; returning facts-only insight")
        self.facts_only = facts_only


def _confidence_from_context(context: list[ContextSnippet], fact: ComputedFact, sources_used: list[str]) -> str:
    """Confidence in the cited explanation, independent of retrieval volume/score."""
    cited = {c.id for c in context} & set(sources_used)
    # Snippets omit tags/dates. Resolve stored IDs within the active dataset and
    # workspace before applying the same recorded-reason rule as the close view.
    doc_ids = {int(s[4:]) for s in cited if re.fullmatch(r"ctx_\d+", s)}
    if doc_ids:
        conn = get_connection()
        try:
            notes = dataset_notes(conn, active_dataset_id(conn))
            if any(d.id in doc_ids and reason_recorded(d, fact.metric, fact.period) for d in notes):
                return "High"
        finally:
            conn.close()
    if cited:
        return "Medium"
    threshold = getattr(fact, "reason_threshold_pct", DEFAULT_REASON_THRESHOLD)
    return "Low" if needs_reason(fact.is_anomaly, fact.deltas.budget_var_pct, threshold) else "Medium"


def _facts_only_output(fact: ComputedFact) -> InsightOutput:
    return InsightOutput(
        metric=fact.metric,
        category=fact.category,
        period=fact.period,
        value=fact.value,
        unit=fact.unit,
        currency=fact.currency,
        deltas=fact.deltas,
        is_anomaly=fact.is_anomaly,
        narrative=None,
        sources=[],
        confidence="Low",
        faithfulness="unchecked",
    )


_NO_CONTEXT_CLOSING = re.compile(
    r"\bunexplained\b|\bno (?:relevant )?context\b|\bcontext\b.*\b(?:does not|doesn't|cannot|can't) explain\b",
    re.I,
)


def _without_context_closing(narrative: str, fact: ComputedFact, streak: TrendStreak | None) -> str:
    """Replace an empty limitation with one available, deterministic observation."""
    sentences = re.split(r"(?<=[.!?])\s+", narrative.strip())
    while sentences and _NO_CONTEXT_CLOSING.search(sentences[-1]):
        sentences.pop()
    text = " ".join(sentences)
    if not text:
        text = f"{fact.metric} was {_fmt(fact.value, fact.unit)}."
    if streak:
        word = "grown" if streak.direction == "growing" else "declined"
        observation = f"It has {word} for {_fmt(streak.months, 'count')} consecutive months."
        present = (r"\bstreak\b|\b(?:consecutive|straight|successive) months?\b|\b(?:\d+|third|three|four|five|six|seven|eight|nine|ten"
                   r"|eleven|twelve|thirteen|fourteen|fifteen|sixteen"
                   r"|seventeen|eighteen|nineteen|twenty(?:[- ](?:one|two|three|four))?)[- ]months?\b")
    elif fact.deltas.yoy_pct is not None:
        if (fact.unit or "").lower() in ("%", "percent", "days"):
            # A rate or a day count changes by points or days, never by a percent of itself.
            change = percent_kpi_points(fact)["yoy"]
            if change is None:
                return text
            unit = "days" if (fact.unit or "").lower() == "days" else "points"
            size = f"{abs(change):.1f}".removesuffix(".0")
            observation = (
                f"It is {size} {unit} {'higher' if change > 0 else 'lower'} than a year ago." if round(change, 1) else "It is level with a year ago."
            )
        else:
            observation = f"It is {_fmt(fact.deltas.yoy_pct, '%')} year over year."
        present = r"\byear[- ]over[- ]year\b|\byoy\b|\b(?:last|previous) year\b|\byear ago\b"
    elif fact.is_anomaly:
        observation = "This period is flagged as an anomaly."
        present = r"\banomal(?:y|ous)\b"
    else:
        return text
    return text if re.search(present, text, re.I) else text.rstrip() + " " + observation


def generate_insight(
    fact: ComputedFact,
    context: list[ContextSnippet],
    llm_client: LLMClient,
    retrieval_scores: list[float] | None = None,
    embedder_semantic: bool = True,
    correlations=None,
    trend_streak=None,
    comparison=None,
    system_prompt: str | None = None,
) -> InsightOutput:
    system_prompt = system_prompt or SYSTEM_PROMPT
    if FIGURE_RULES not in system_prompt:
        system_prompt += "\n\n" + FIGURE_RULES
    user_prompt = build_user_prompt(fact, context, correlations, trend_streak, comparison)

    t0 = time.perf_counter()
    try:
        result, usage = llm_client.generate_narrative(system_prompt, user_prompt)
    except LLMGenerationError:
        raise GenerationFailedFactsOnly(_facts_only_output(fact))

    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    cost_usd = usage.cost_usd
    passed, unverified = check_faithfulness(result.narrative, fact, context)

    if not passed:
        # Regenerate once with a stricter reminder before giving up (PRD 6.4).
        stricter_user_prompt = (
            user_prompt + "\n\nYour previous attempt used an unverifiable figure. Rewrite using ONLY "
            "the numbers listed in Computed facts above, copying each figure exactly as given."
        )
        try:
            result, usage = llm_client.generate_narrative(system_prompt, stricter_user_prompt)
        except LLMGenerationError:
            raise GenerationFailedFactsOnly(_facts_only_output(fact))
        prompt_tokens += usage.prompt_tokens
        completion_tokens += usage.completion_tokens
        cost_usd = cost_usd + usage.cost_usd if cost_usd is not None and usage.cost_usd is not None else None
        passed, unverified = check_faithfulness(result.narrative, fact, context)

    context_by_id = {c.id: c for c in context}
    sources = [SourceRef(id=cid, type=context_by_id[cid].type, title=context_by_id[cid].title) for cid in result.sources_used if cid in context_by_id]

    narrative = _unsigned_levels(result.narrative)
    if not sources:
        sentences = re.split(r"(?<=[.!?])\s+", narrative.strip())
        while sentences and re.match(r"^(?:Watch for\b|Monitor\b|Next month,)", sentences[-1], re.I):
            sentences.pop()
        narrative = " ".join(sentences)
        narrative = _without_context_closing(narrative, fact, trend_streak)
    passed, unverified = check_faithfulness(narrative, fact, context)

    confidence = "Low" if not passed else _confidence_from_context(context, fact, result.sources_used)

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return InsightOutput(
        metric=fact.metric,
        category=fact.category,
        period=fact.period,
        value=fact.value,
        unit=fact.unit,
        currency=fact.currency,
        deltas=fact.deltas,
        is_anomaly=fact.is_anomaly,
        narrative=narrative,
        sources=sources,
        confidence=confidence,
        faithfulness="passed" if passed else "failed",
        faithfulness_messages=faithfulness_messages(narrative, fact, context),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        prompt_version=PROMPT_VERSION,
        correlations=correlations or [],
        trend_streak=trend_streak,
    )


def _unsigned_levels(text: str) -> str:
    def unsigned(match):
        tail = text[match.end():]
        if re.match(r"\s*(?:year|month|quarter|higher|lower|versus|vs\b)", tail, re.I):
            return match.group(0)
        return match.group(1) + match.group(2)
    return re.sub(r"(\b(?:was|is|at)\s+)\+(\d+(?:\.\d+)?%?)", unsigned, text, flags=re.I)
