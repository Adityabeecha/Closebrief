"""Behavioral regressions from the generation and UI follow-up audit."""

import pytest

from app.generation.guard import check_faithfulness
from app.grounding import attribute
from app.schemas import ComputedFact, ContextSnippet, Deltas


def fact(**overrides):
    values = dict(metric="Revenue", period="2025-03", value=100_000,
                  deltas=Deltas(mom_pct=10))
    values.update(overrides)
    return ComputedFact(**values)


@pytest.mark.parametrize("narrative", ["Revenue rose 3%.", "Revenue was $2025.",
                                        "We lost 3 accounts."])
def test_period_digits_do_not_license_kpi_claims(narrative):
    assert check_faithfulness(narrative, fact())[0] is False
    assert attribute(narrative, fact())["faithful"] is False


@pytest.mark.parametrize("body,narrative", [("The fee was $47.", "Revenue rose 47%."),
                                           ("Prices rose 47%.", "We lost $47.")])
def test_context_cannot_change_numeric_units(body, narrative):
    context = [ContextSnippet(id="ctx_1", body=body)]
    assert check_faithfulness(narrative, fact(), context)[0] is False
    assert attribute(narrative, fact(), context)["faithful"] is False


def test_percent_metric_attribution_agrees_with_guard():
    metric = fact(metric="Margin", unit="%", value=62)
    assert check_faithfulness("Margin was 62%.", metric)[0] is True
    result = attribute("Margin was 62%.", metric)
    assert result["faithful"] is True
    assert result["sentences"][0]["facts"][0]["field"] == "current value"


def test_multiplier_claims_keep_the_direction_of_change():
    metric = fact(deltas=Deltas(mom_pct=-100))
    assert check_faithfulness("Revenue doubled.", metric)[0] is False
    assert attribute("Revenue doubled.", metric)["faithful"] is False


def test_retry_usage_includes_both_paid_calls():
    from app.generation.generate import generate_insight
    from app.generation.llm_client import GenerationResult, TokenUsage

    class Client:
        responses = iter([
            (GenerationResult(narrative="Revenue rose 47%."),
             TokenUsage(prompt_tokens=100, completion_tokens=10, cost_usd=0.01)),
            (GenerationResult(narrative="Revenue rose 10%."),
             TokenUsage(prompt_tokens=150, completion_tokens=20, cost_usd=0.02)),
        ])

        def generate_narrative(self, system, user):
            return next(self.responses)

    result = generate_insight(fact(), [], Client())
    assert result.faithfulness == "passed"
    assert (result.prompt_tokens, result.completion_tokens) == (250, 30)
    assert result.cost_usd == pytest.approx(0.03)
