"""v3.0: the shared answer_question() Q&A logic (fake LLM, no network)."""

from app.generation.qa import answer_question
from app.schemas import ComputedFact, ContextSnippet, Deltas


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5
    cost_usd = 0.001


class _Result:
    def __init__(self, narrative, sources_used=None):
        self.narrative = narrative
        self.sources_used = sources_used or []


class _FakeLLM:
    def __init__(self, answers):
        self.answers = answers
        self.calls = 0

    def generate_narrative(self, system, prompt):
        a = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return _Result(a), _Usage()


def _fact():
    return ComputedFact(
        metric="Net Revenue", category="Revenue", period="2025-03", value=4_200_000.0,
        unit="USD", prior_value=4_772_727.0,
        deltas=Deltas(mom_pct=-12.0, yoy_pct=4.5, budget_var_abs=-370000.0, budget_var_pct=-8.1),
        trend=None, is_anomaly=True,
    )


def _ctx():
    return [ContextSnippet(id="ctx_001", type="event_note", title="March 2025 pricing change",
                           body="Enterprise prices rose 15 percent on March 1 2025.")]


def test_clean_answer_no_retry():
    llm = _FakeLLM(["Revenue fell, consistent with the pricing change."])
    qa = answer_question(llm, _fact(), _ctx(), "Why did revenue fall?")
    assert llm.calls == 1 and qa.grounded is True
    assert qa.prompt_tokens == 10 and qa.cost_usd == 0.001


def test_unfaithful_triggers_one_retry_and_accumulates_cost():
    llm = _FakeLLM([
        "Revenue was exactly $9,999,999 this month.",   # invented → unfaithful
        "Revenue declined, aligning with the pricing change.",
    ])
    qa = answer_question(llm, _fact(), _ctx(), "Why?")
    assert llm.calls == 2 and qa.grounded is True
    assert "9,999,999" not in qa.answer
    assert qa.prompt_tokens == 20 and qa.cost_usd == 0.002   # both attempts summed
