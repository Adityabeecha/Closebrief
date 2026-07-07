"""facts + context -> narrative. Orchestrates prompt build, LLM call, and the
faithfulness guard. Never lets an unverified number leave the system."""

import time

from app.generation.guard import check_faithfulness
from app.generation.llm_client import LLMClient, LLMGenerationError
from app.generation.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from app.schemas import ComputedFact, ContextSnippet, InsightOutput, SourceRef


class GenerationFailedFactsOnly(Exception):
    """Raised when the LLM is unreachable. Carries the computed facts so the
    caller can still render a facts-only card (PRD US-C1 AC)."""

    def __init__(self, facts_only: InsightOutput):
        super().__init__("LLM generation failed; returning facts-only insight")
        self.facts_only = facts_only


def _confidence_from_context(
    context: list[ContextSnippet], scores: list[float] | None = None
) -> str:
    """US-C1: Low when fewer than 2 relevant context chunks were retrieved;
    otherwise Medium/High per retrieval score."""
    if len(context) < 2:
        return "Low"
    if scores:
        top = max(scores)
        if len(context) >= 3 and top >= 0.35:
            return "High"
        if top >= 0.25:
            return "High" if len(context) >= 3 else "Medium"
        return "Medium"
    return "High" if len(context) >= 3 else "Medium"


def _facts_only_output(fact: ComputedFact) -> InsightOutput:
    return InsightOutput(
        metric=fact.metric,
        category=fact.category,
        period=fact.period,
        value=fact.value,
        unit=fact.unit,
        deltas=fact.deltas,
        is_anomaly=fact.is_anomaly,
        narrative=None,
        sources=[],
        confidence="Low",
        faithfulness="unchecked",
    )


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
    user_prompt = build_user_prompt(fact, context, correlations, trend_streak, comparison)

    t0 = time.perf_counter()
    try:
        result, usage = llm_client.generate_narrative(system_prompt, user_prompt)
    except LLMGenerationError:
        raise GenerationFailedFactsOnly(_facts_only_output(fact))

    passed, unverified = check_faithfulness(result.narrative, fact, context)

    if not passed:
        # Regenerate once with a stricter reminder before giving up (PRD 6.4).
        stricter_user_prompt = (
            user_prompt
            + "\n\nYour previous attempt used unverifiable number(s): "
            + ", ".join(str(v) for v in unverified)
            + ". Rewrite using ONLY the numbers listed in Computed facts above."
        )
        try:
            result, usage = llm_client.generate_narrative(system_prompt, stricter_user_prompt)
        except LLMGenerationError:
            raise GenerationFailedFactsOnly(_facts_only_output(fact))
        passed, unverified = check_faithfulness(result.narrative, fact, context)

    context_by_id = {c.id: c for c in context}
    sources = [
        SourceRef(id=cid, type=context_by_id[cid].type, title=context_by_id[cid].title)
        for cid in result.sources_used
        if cid in context_by_id
    ]

    confidence = (
        "Low" if not passed else _confidence_from_context(context, retrieval_scores)
    )
    # Non-semantic embedder (offline hashing fallback): its similarity scores
    # are noise, so they must not earn High confidence.
    if not embedder_semantic and confidence == "High":
        confidence = "Medium"
    # Context was provided but the narrative cites none of it: the causes are
    # not attributable, so knock confidence down one notch.
    if passed and context and not sources:
        confidence = {"High": "Medium", "Medium": "Low", "Low": "Low"}[confidence]

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return InsightOutput(
        metric=fact.metric,
        category=fact.category,
        period=fact.period,
        value=fact.value,
        unit=fact.unit,
        deltas=fact.deltas,
        is_anomaly=fact.is_anomaly,
        narrative=result.narrative,
        sources=sources,
        confidence=confidence,
        faithfulness="passed" if passed else "failed",
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_usd=usage.cost_usd,
        latency_ms=latency_ms,
        prompt_version=PROMPT_VERSION,
        correlations=correlations or [],
        trend_streak=trend_streak,
    )
