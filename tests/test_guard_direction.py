"""P2: a figure must match to the precision it's written in, and rise/fall wording
must agree with the sign of the computed change it cites."""

import pytest

from app.generation.guard import check_faithfulness, faithfulness_messages
from app.grounding.attribution import attribute
from app.schemas import ComputedFact

# Net Revenue fell 12% vs last month, rose 4.5% vs last year, and was 8.1% ($370K) under plan.
FACT = ComputedFact(metric="Net Revenue", category="Revenue", period="2025-03", value=4200000,
                    prior_value=4772727, unit="USD",
                    deltas={"mom_pct": -12.0, "yoy_pct": 4.5, "budget_var_abs": -370000, "budget_var_pct": -8.1},
                    trend="up", is_anomaly=True)


@pytest.mark.parametrize("text", [
    "Net Revenue fell 12% from last month to $4.20M.",
    "Net Revenue was $4.2M, 8.1% below plan.",
    "Net Revenue declined 12.0% but was 4.5% higher than last year.",
    "Net Revenue came in at $4.20M, −8.1% vs plan.",
    "Net Revenue fell to $4.2M.",
    "Net Revenue missed plan by $370K.",
    "Revenue was 8.1% under budget.",
])
def test_correct_direction_passes(text):
    assert check_faithfulness(text, FACT)[0], text


@pytest.mark.parametrize("text", [
    "Net Revenue rose 12% from last month.",           # it fell 12%
    "Net Revenue was 8.1% ahead of plan.",              # it was 8.1% behind
    "Net Revenue came in at $4.20M, +8.1% vs plan.",    # explicit sign is wrong
    "Net Revenue rose to $4.2M.",                       # the level fell from $4.77M
    "Revenue was 8.1% over budget.",
    "Net Revenue fell 4.5% versus last year.",          # it rose 4.5%
])
def test_wrong_direction_fails(text):
    passed, unverified = check_faithfulness(text, FACT)
    assert not passed and unverified, text


def test_numbers_without_direction_words_are_judged_on_value_only():
    assert check_faithfulness("Net Revenue moved 12% month over month.", FACT)[0]


def test_written_precision_is_the_tolerance():
    assert check_faithfulness("Net Revenue was $4.20M.", FACT)[0]
    assert check_faithfulness("Net Revenue was $4.2M.", FACT)[0]
    # The old 2% tolerance let $4.28M stand for $4.20M; to two decimals it can't.
    assert not check_faithfulness("Net Revenue was $4.28M.", FACT)[0]
    # A coarse "$4M" for $4.2M overstates the rounding (more than 2.5% off).
    assert not check_faithfulness("Net Revenue was $4M.", FACT)[0]
    assert not check_faithfulness("Net Revenue fell 11.8% from last month.", FACT)[0]


def test_message_explains_a_direction_mistake():
    msgs = faithfulness_messages("Net Revenue rose 12% from last month.", FACT)
    assert any("calls 12% a rise" in m and "fell" in m for m in msgs), msgs


def test_sentence_highlighting_marks_a_direction_mistake():
    out = attribute("Net Revenue rose 12% from last month.", FACT)
    assert not out["faithful"]
    assert out["sentences"][0]["kind"] == "numeric-unverified"


def test_percent_written_as_a_word_counts_as_a_percentage():
    from app.schemas import ContextSnippet
    ctx = [ContextSnippet(id="c1", type="event_note", title="Pricing", body="Enterprise prices rose 15 percent on March 1.")]
    assert check_faithfulness("Churn rose after prices rose 15% on March 1.", FACT, ctx)[0]
    assert check_faithfulness("Net Revenue fell 12 percent from last month.", FACT)[0]
    assert not check_faithfulness("Net Revenue rose 12 percent from last month.", FACT)[0]


def test_source_ids_in_prose_are_not_figures():
    # The model sometimes cites "(id: ctx_001)"; 001 is an id, not an invented number.
    assert check_faithfulness("Revenue fell 12% after the pricing change (id: ctx_001).", FACT)[0]
