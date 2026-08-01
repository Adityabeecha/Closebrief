import pytest

from app.generation.guard import check_faithfulness
from app.schemas import ComputedFact, ContextSnippet, Deltas


def _legal_aug_fact():
    return ComputedFact(
        metric="Legal & Professional Fees",
        period="2025-08",
        value=1388577.82,
        prior_value=224821.20,
        unit="USD",
        deltas=Deltas(
            mom_pct=517.64,
            yoy_pct=None,
            budget_var_abs=77.82,
            budget_var_pct=0.0056,
        ),
        trend="up",
        is_anomaly=True,
    )


def test_settlement_narrative_with_correct_figures_passes():
    fact = _legal_aug_fact()
    narrative = (
        "Legal & Professional Fees reached $1,388,577.82 in August, up from "
        "$224,821.20 the prior month, a 517.64% increase."
    )
    passed, unverified = check_faithfulness(narrative, fact)
    assert passed, unverified


def test_invented_multiplier_for_the_settlement_is_rejected():
    fact = _legal_aug_fact()
    narrative = "Legal fees tripled in August to $1,388,577.82."
    passed, unverified = check_faithfulness(narrative, fact)
    assert not passed
    assert 200.0 in unverified


def test_invented_magnitude_is_rejected():
    fact = _legal_aug_fact()
    narrative = "Legal fees rose to $1,450,000 in August on a one-time settlement."
    passed, unverified = check_faithfulness(narrative, fact)
    assert not passed


def test_uncovered_movement_cannot_borrow_a_number_from_nowhere():
    fact = _legal_aug_fact()
    narrative = (
        "Legal & Professional Fees reached $1,388,577.82 in August, driven by a "
        "$1,200,000 settlement with a former reseller."
    )
    passed, unverified = check_faithfulness(narrative, fact)
    assert not passed
    assert any(abs(v) == pytest.approx(1200000.0) for v in unverified)


def test_a_figure_that_only_exists_in_context_is_accepted():
    fact = _legal_aug_fact()
    context = [ContextSnippet(
        id="board-note-2025-08",
        title="Board note: litigation settlement",
        body="The settlement was agreed at $1,200,000 plus $188,577 of related counsel fees.",
    )]
    narrative = (
        "Legal & Professional Fees reached $1,388,577.82 in August, driven by a "
        "$1,200,000 settlement."
    )
    passed, unverified = check_faithfulness(narrative, fact, context=context)
    assert passed, unverified


def test_context_numbers_are_held_to_the_same_tolerance_as_facts():
    fact = _legal_aug_fact()
    context = [ContextSnippet(
        id="board-note-2025-08",
        title="Board note",
        body="The settlement was agreed at $1,200,000.",
    )]

    faithful = "August legal fees of $1,388,577.82 include a $1,200,000 settlement."
    assert check_faithfulness(faithful, fact, context=context)[0]

    drifted = "August legal fees of $1,388,577.82 include a $1,320,000 settlement."
    passed, unverified = check_faithfulness(drifted, fact, context=context)
    assert not passed
    assert any(abs(v) == pytest.approx(1320000.0) for v in unverified)


def test_a_false_but_retrievable_context_figure_passes_the_guard():
    fact = _legal_aug_fact()
    wrong_context = [ContextSnippet(
        id="stale-deck",
        title="Q2 board deck (superseded)",
        body="Legal exposure for the quarter is estimated at $450,000.",
    )]
    narrative = (
        "Legal & Professional Fees reached $1,388,577.82 in August against an "
        "estimated exposure of $450,000."
    )
    passed, _ = check_faithfulness(narrative, fact, context=wrong_context)
    assert passed


def test_percent_and_magnitude_buckets_do_not_cross_verify():
    fact = _legal_aug_fact()
    narrative = "Legal fees moved 1388577.82% in August."
    passed, unverified = check_faithfulness(narrative, fact)
    assert not passed


def test_period_year_and_month_are_not_treated_as_invented_figures():
    fact = _legal_aug_fact()
    narrative = (
        "In August 2025, Legal & Professional Fees reached $1,388,577.82 over a "
        "12-month trend."
    )
    passed, unverified = check_faithfulness(narrative, fact)
    assert passed, unverified
