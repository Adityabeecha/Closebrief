"""Grounded metric Q&A (v3.0). The single source of truth for how a follow-up
question is answered: generate once, and if the answer isn't numerically
faithful, retry once with the offending numbers called out. Shared by the /ask
endpoint and the Q&A eval so both measure the same behavior.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.generation.guard import check_faithfulness
from app.generation.llm_client import LLMGenerationError
from app.generation.prompts import QA_SYSTEM_PROMPT, build_qa_prompt
from app.schemas import ComputedFact, ContextSnippet


@dataclass
class QAResult:
    answer: str
    sources_used: list[str]
    grounded: bool
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


def answer_question(llm_client, fact: ComputedFact, context: list[ContextSnippet],
                    question: str, history: list[dict] | None = None) -> QAResult:
    """Answer `question` about one metric under the faithfulness guarantee.

    Raises LLMGenerationError if the first generation fails (caller decides how to
    surface it). A faithfulness miss triggers exactly one stricter retry; the
    retry is preferred whether it comes back clean or as the honest fallback.
    """
    base = build_qa_prompt(fact, context, question, history)
    result, usage = llm_client.generate_narrative(QA_SYSTEM_PROMPT, base)
    passed, unverified = check_faithfulness(result.narrative, fact, context)
    p = usage.prompt_tokens or 0
    c = usage.completion_tokens or 0
    cost = usage.cost_usd or 0.0

    if not passed:
        stricter = (base + "\n\nYour previous answer used unverifiable number(s): "
                    + ", ".join(str(v) for v in unverified)
                    + '. Rewrite using ONLY the numbers in the Computed facts block, or say '
                      '"The data available doesn\'t answer that."')
        try:
            retry, usage2 = llm_client.generate_narrative(QA_SYSTEM_PROMPT, stricter)
            p += usage2.prompt_tokens or 0
            c += usage2.completion_tokens or 0
            cost += usage2.cost_usd or 0.0
            passed, _ = check_faithfulness(retry.narrative, fact, context)
            result = retry
        except LLMGenerationError:
            pass

    return QAResult(
        answer=result.narrative or "",
        sources_used=list(result.sources_used or []),
        grounded=passed, prompt_tokens=p, completion_tokens=c, cost_usd=cost,
    )
