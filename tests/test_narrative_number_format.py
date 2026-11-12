"""Narrative figures use the cards' formats without weakening numeric grounding."""

import re

import pytest

from app.generation.generate import generate_insight
from app.generation.guard import check_faithfulness, extract_numbers
from app.generation.llm_client import GenerationResult, TokenUsage
from app.generation.prompts import (
    _fmt,
    build_comparison_context,
    build_forecast_prompt,
    build_funnel_prompt,
    build_root_cause_prompt,
    build_user_prompt,
)
from app.grounding import attribute
from app.schemas import ComputedFact, ContextSnippet, Deltas, PeriodComparison, TrendStreak


def hosting_fact(**overrides):
    values = dict(metric="Cloud Hosting & Infrastructure", period="2026-06", unit="USD",
                  value=271254.84, prior_value=268038.38,
                  deltas=Deltas(mom_pct=1.1999997910746822, budget_var_abs=-245.16, budget_var_pct=-0.0902983425))
    values.update(overrides)
    return ComputedFact(**values)


@pytest.mark.parametrize("value,unit,want", [
    (1200000, "USD", "$1.20M"), (271254.84, "USD", "$271K"),
    # F3: in full under 100K. "$3K" for $2,500 overstated it by a fifth.
    (1499, "USD", "$1,499"), (1500, "USD", "$1,500"), (2500, "USD", "$2,500"), (12345.6, "USD", "$12,346"),
    (99999.4, "USD", "$99,999"), (100000, "USD", "$100K"), (-1450, "USD", "−$1,450"),
    (12.5, "USD", "$12.50"), (0, "USD", "$0.00"),
    (-370000, "USD", "\u2212$370K"), (-12.5, "USD", "\u2212$12.50"),
    (1.1999997910746822, "%", "+1.2%"), (-0.0902983425, "%", "\u22120.1%"),
    (0, "%", "0.0%"), (62.25, "%", "+62.3%"),
    (1234.5, "count", "1,235"), (-1234.5, "count", "\u22121,234"),
    (None, "USD", "n/a"), (float("inf"), "USD", "n/a"), (float("nan"), "USD", "n/a"),
])
def test_prompt_formats_card_figures(value, unit, want):
    assert _fmt(value, unit) == want


def test_computed_fact_inputs_are_formatted_before_generation():
    prompt = build_user_prompt(hosting_fact(variance_bridge={"price": 125000, "volume": -1200000}), [])
    for line in ["value: $271K", "prior_period_value: $268K", "month_over_month_change: +1.2%",
                 "budget_variance_absolute: \u2212$245.16", "budget_variance_percent: \u22120.1%",
                 "price_effect: $125K", "volume_effect: \u2212$1.20M"]:
        assert line in prompt
    assert "271,254.84" not in prompt and "USD" not in prompt
    assert not re.search(r"\d\.\d{3,}", prompt)


@pytest.mark.parametrize("unit,value,want", [("count", 12345.6, "12,346"), ("%", 62.25, "+62.3%")])
def test_non_money_metric_inputs_keep_their_units(unit, value, want):
    prompt = build_user_prompt(hosting_fact(unit=unit, value=value, prior_value=None, deltas=Deltas()), [])
    assert f"value: {want}" in prompt
    assert "$" not in prompt


@pytest.mark.parametrize("value,text,passed", [
    (271254.84, "$271K", True), (271254.84, "$300K", False),
    (-271254.84, "-$271K", True), (-271254.84, "\u2212$271K", True),
    (1499, "$1K", True), (1499, "$2K", False),
    (1500, "$2K", True), (1500, "$1K", False),
    (2500, "$3K", True), (2500, "$2K", False),
    (10049, "$10K", True), (10049, "$10.5K", False),
    (1204999, "$1.20M", True), (1204999, "$1.50M", False),
    (1499999, "$1M", False), (600, "$1K", False),
])
def test_guard_and_attribution_accept_only_grounded_rounded_figures(value, text, passed):
    fact = hosting_fact(value=value, prior_value=None, deltas=Deltas())
    assert check_faithfulness(f"The value was {text}.", fact)[0] is passed
    assert attribute(f"The value was {text}.", fact)["faithful"] is passed


@pytest.mark.parametrize("text", ["-$271K, -1.2%", "\u2212$271K, \u22121.2%", "+$271K, +1.2%"])
def test_extraction_preserves_ascii_and_real_minus_signs(text):
    expected_sign = 1 if text.startswith("+") else -1
    assert extract_numbers(text) == [(expected_sign * 271000.0, False), (expected_sign * 1.2, True)]


def test_compact_money_rounding_does_not_widen_percent_tolerance():
    fact = hosting_fact(value=10, prior_value=None, deltas=Deltas(mom_pct=1499))
    assert check_faithfulness("Revenue rose 2000%.", fact)[0] is False
    assert check_faithfulness("Revenue rose 1%.", hosting_fact(value=1499, deltas=Deltas()))[0] is False


def test_comparison_figures_use_metric_units_and_signed_percentages():
    comparison = PeriodComparison(metric="Accounts", period_a="2026-05", period_b="2026-06",
                                  value_a=1000, value_b=2234.5, abs_change=1234.5, pct_change=123.45,
                                  mom_pct_a=-4.54, mom_pct_b=1.25, acceleration=5.79, momentum="accelerating")
    text = "\n".join(build_comparison_context(comparison, "count"))
    assert "1,235" in text and "+123.5%" in text and "\u22124.5%" in text and "+1.3%" in text
    assert "$" not in text


def test_forecast_and_funnel_inputs_are_formatted():
    forecast = build_forecast_prompt("Revenue", "USD", [{"period":"2026-06","value":271254.84}],
                                     [{"period":"2026-07","value":1200000,"budget":1150000}], 3.54)
    assert "$271K" in forecast and "$1.20M" in forecast and "$1.15M" in forecast and "+3.5%" in forecast
    count_forecast = build_forecast_prompt("Accounts", "count", [], [{"period":"2026-07","value":1234.5}], None)
    assert "1,235" in count_forecast and "$" not in count_forecast
    funnel = build_funnel_prompt({"period":"2026-06","stages":[{"name":"Leads","value":1234.5,
                                  "conversion_from_prev":12.3456,"conversion_mom_pp":-1.25,"drop_off":234.5}],
                                 "overall_conversion":4.567})
    assert "1,235" in funnel and "+12.3%" in funnel and "\u22121.3pp" in funnel and "+4.6%" in funnel
    assert "$" not in funnel and "12.3456" not in funnel


def test_root_cause_inputs_keep_amounts_and_percentages_consistent():
    prompt = build_root_cause_prompt({"metric":"Revenue","period":"2026-06","unit":"USD","value":271254.84,
                                     "prior_value":268038.38,"mom_pct":1.199999791,"budget_var_pct":-0.0902983,
                                     "budget_var_abs":-245.16,"pvm":[{"component":"Price","impact":1200000,"share_pct":62.25}]})
    assert "$271K" in prompt and "$268K" in prompt and "+1.2%" in prompt
    assert "\u2212$245.16" in prompt and "\u22120.1%" in prompt and "$1.20M" in prompt and "+62.3%" in prompt


class FakeLLM:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def generate_narrative(self, system, user):
        self.calls.append((system, user))
        return next(self.responses), TokenUsage(prompt_tokens=10, completion_tokens=5, cost_usd=0.01)


@pytest.mark.parametrize("system_prompt", [None, "Write for marketing leaders."])
def test_generation_supplies_formatted_figures_and_exact_copy_rule(system_prompt):
    llm = FakeLLM(GenerationResult(narrative="Hosting costs reached $271K, up +1.2%."))
    result = generate_insight(hosting_fact(), [], llm, system_prompt=system_prompt)
    system, user = llm.calls[0]
    assert re.search(r"copy.*figures.*exactly", system, re.I)
    assert "$271K" in user and "+1.2%" in user and "271,254.84" not in user
    assert result.faithfulness == "passed"


@pytest.mark.parametrize("closing", [
    "The drivers of this movement remain unexplained in this period.",
    "The movement is unexplained this period.",
    "No context explains the move this period.",
])
@pytest.mark.parametrize("extra,streak,observation", [
    ({}, None, None),
    ({}, TrendStreak(metric="Cloud Hosting & Infrastructure", direction="growing", months=4,
                     start_period="2026-03", end_period="2026-06"), "has grown for 4 consecutive months"),
    ({}, TrendStreak(metric="Cloud Hosting & Infrastructure", direction="declining", months=3,
                     start_period="2026-04", end_period="2026-06"), "has declined for 3 consecutive months"),
    ({"deltas":Deltas(yoy_pct=37.59)}, None, "+37.6% year over year"),
    ({"deltas":Deltas(yoy_pct=-4.54)}, None, "\u22124.5% year over year"),
    ({"is_anomaly":True}, None, "flagged as an anomaly"),
])
def test_no_context_closing_uses_an_available_fact_instead_of_boilerplate(closing, extra, streak, observation):
    llm = FakeLLM(GenerationResult(narrative="Hosting costs reached $271K. " + closing))
    result = generate_insight(hosting_fact(**extra), [], llm, trend_streak=streak)
    assert "unexplained" not in result.narrative.lower() and "No context" not in result.narrative
    assert result.narrative.startswith("Hosting costs reached $271K.")
    if observation:
        assert observation in result.narrative
        assert len(re.split(r"(?<=[.!?])\s+", result.narrative)) == 2
    else:
        assert result.narrative == "Hosting costs reached $271K."
    assert result.faithfulness == "passed"


def test_existing_useful_observation_is_not_duplicated():
    text = "Hosting costs reached $271K. It is +37.6% year over year."
    llm = FakeLLM(GenerationResult(narrative=text))
    result = generate_insight(hosting_fact(deltas=Deltas(yoy_pct=37.59)), [], llm)
    assert result.narrative == text


def test_irrelevant_context_does_not_require_an_unexplained_closing():
    context = [ContextSnippet(id="ctx-1", body="An unrelated hiring policy.")]
    llm = FakeLLM(GenerationResult(narrative="Hosting costs reached $271K. The movement remains unexplained this period."))
    result = generate_insight(hosting_fact(is_anomaly=True), context, llm)
    assert "unexplained" not in result.narrative and "flagged as an anomaly" in result.narrative
    assert result.sources == []


def test_cited_context_explanation_is_preserved():
    context = [ContextSnippet(id="ctx-1", title="Pricing", body="A pricing increase lifted hosting costs.")]
    text = "Hosting costs reached $271K. This reflects the pricing increase."
    llm = FakeLLM(GenerationResult(narrative=text, sources_used=["ctx-1"]))
    result = generate_insight(hosting_fact(is_anomaly=True), context, llm)
    assert result.narrative == text and result.sources[0].id == "ctx-1"


def test_single_boilerplate_sentence_cannot_leave_an_empty_narrative():
    llm = FakeLLM(GenerationResult(narrative="The movement remains unexplained this period."))
    result = generate_insight(hosting_fact(), [], llm)
    assert "unexplained" not in result.narrative
    assert "$271K" in result.narrative and result.faithfulness == "passed"


def test_retry_uses_formatted_facts_without_reintroducing_raw_numbers():
    llm = FakeLLM(GenerationResult(narrative="Hosting costs reached $300K."),
                  GenerationResult(narrative="Hosting costs reached $271K."))
    result = generate_insight(hosting_fact(), [], llm)
    assert len(llm.calls) == 2 and result.faithfulness == "passed"
    assert "$271K" in llm.calls[1][1] and "300000.0" not in llm.calls[1][1]
    assert result.cost_usd == pytest.approx(0.02)


@pytest.mark.parametrize("unit", ["%", "percent"])
def test_rate_metric_absolute_differences_keep_percentage_point_units(unit):
    fact = hosting_fact(metric="Gross Margin", value=62.25, prior_value=60, unit=unit,
                        deltas=Deltas(budget_var_abs=7.25, budget_var_pct=13.18),
                        variance_bridge={"price":1.25})
    prompt = build_user_prompt(fact, [])
    # F11: a rate's variance is given in points only; its relative change (+13.2%)
    # would read as "margin up 13%" and is no longer offered to the model.
    assert "value: +62.3%" in prompt and "budget_variance_percent" not in prompt and "+13.2%" not in prompt
    assert "budget_variance: +7.3pp" in prompt and "month_over_month_change: +2.3pp" in prompt
    assert "price_effect: +1.3pp" in prompt
    text = "Margin reached +62.3%, an absolute budget variance of +7.3pp with a +1.3pp price effect."
    llm = FakeLLM(GenerationResult(narrative=text))
    result = generate_insight(fact, [], llm)
    assert result.faithfulness == "passed" and attribute(result.narrative, fact)["faithful"]


@pytest.mark.parametrize("unit,value", [("USD", 1499), ("USD", -2500), ("USD", 271254.84),
                                        ("USD", 1204999), ("%", 62.25), ("count", 1234.5)])
def test_guard_accepts_the_exact_figure_supplied_by_the_formatter(unit, value):
    fact = hosting_fact(unit=unit, value=value, prior_value=None, deltas=Deltas())
    text = f"The value is {_fmt(value, unit)}."
    assert check_faithfulness(text, fact)[0] and attribute(text, fact)["faithful"]
