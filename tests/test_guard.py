from app.generation.guard import check_faithfulness, extract_numbers
from app.schemas import ComputedFact, ContextSnippet, Deltas


def make_fact(**overrides) -> ComputedFact:
    defaults = dict(
        metric="Net Revenue",
        period="2025-03",
        value=4_200_000.0,
        prior_value=4_772_727.0,
        unit="USD",
        deltas=Deltas(
            mom_pct=-12.0,
            yoy_pct=4.5,
            budget_var_abs=-370_000.0,
            budget_var_pct=-8.1,
        ),
        trend="down",
        is_anomaly=True,
    )
    defaults.update(overrides)
    return ComputedFact(**defaults)


def test_extract_numbers_handles_currency_and_suffixes():
    nums = extract_numbers("Revenue fell 12% to $4.2M, which is $370,000 below plan.")
    assert (12.0, True) in nums
    assert (4_200_000.0, False) in nums
    assert (370_000.0, False) in nums


def test_faithful_narrative_passes():
    narrative = (
        "Net revenue fell 12% month over month to $4.2M, 8.1% below plan, "
        "driven mainly by enterprise churn following the March price change."
    )
    passed, unverified = check_faithfulness(narrative, make_fact())
    assert passed, f"unexpected unverified numbers: {unverified}"


def test_invented_number_fails():
    narrative = "Net revenue fell 12% because we lost 47 enterprise accounts."
    passed, unverified = check_faithfulness(narrative, make_fact())
    assert not passed
    assert 47.0 in unverified


def test_invented_percentage_fails():
    narrative = "Net revenue fell 33% month over month to $4.2M."
    passed, unverified = check_faithfulness(narrative, make_fact())
    assert not passed
    assert 33.0 in unverified


def test_rounding_within_tolerance_passes():
    # -8.1% stated as "8%" is within the 2% relative + 0.5 absolute floor.
    narrative = "Net revenue came in 8% below plan at $4.2M."
    passed, unverified = check_faithfulness(narrative, make_fact())
    assert passed, f"unexpected unverified numbers: {unverified}"


def test_period_year_not_flagged():
    narrative = "In March 2025, net revenue fell 12% to $4.2M."
    passed, unverified = check_faithfulness(narrative, make_fact())
    assert passed, f"unexpected unverified numbers: {unverified}"


def test_narrative_without_numbers_passes():
    narrative = "Net revenue declined this month, driven by churn after the price change."
    passed, unverified = check_faithfulness(narrative, make_fact())
    assert passed


def test_multiplier_word_matching_fact_passes():
    # mom_pct is +61.2 in this fact variant -> "doubled" (~100%) should NOT
    # match, but a fact that truly doubled should pass.
    fact = make_fact()
    fact.deltas.mom_pct = 100.0
    narrative = "Revenue doubled month over month to $4.2M."
    passed, unverified = check_faithfulness(narrative, fact)
    assert passed, unverified


def test_multiplier_word_without_matching_fact_fails():
    narrative = "Net revenue tripled this month to $4.2M."
    passed, unverified = check_faithfulness(narrative, make_fact())
    assert not passed
    assert 200.0 in unverified


def test_percent_does_not_match_magnitude_bucket():
    # 6.2 as a percent should NOT match any magnitude or percent fact,
    # even though magnitudes like $4.2M exist in a different bucket.
    narrative = "Net revenue fell 6.2% month over month."
    passed, unverified = check_faithfulness(narrative, make_fact())
    assert not passed
    assert 6.2 in unverified


def test_duration_and_calendar_day_not_flagged():
    # "12-month trend" and "March 1, 2025" are durations/dates, not KPI figures.
    narrative = (
        "Over the 12-month trend net revenue is up, but it fell 12% to $4.2M "
        "following the March 1, 2025 price change."
    )
    passed, unverified = check_faithfulness(narrative, make_fact())
    assert passed, f"unexpected unverified numbers: {unverified}"


def test_number_from_context_is_allowed():
    # A figure the model cites from provided context counts as grounded.
    context = [
        ContextSnippet(
            id="ctx_1",
            type="event_note",
            title="Pricing change",
            body="On 1 March 2025 we raised enterprise prices 15%.",
        )
    ]
    narrative = "Net revenue fell 12% to $4.2M after the 15% enterprise price increase."
    passed, unverified = check_faithfulness(narrative, make_fact(), context)
    assert passed, f"unexpected unverified numbers: {unverified}"


def test_invented_number_still_fails_with_context():
    # The context does not mention 47; it must still be caught.
    context = [
        ContextSnippet(id="ctx_1", type="event_note", title="x", body="Prices rose 15%.")
    ]
    narrative = "Net revenue fell 12% because we lost 47 enterprise accounts."
    passed, unverified = check_faithfulness(narrative, make_fact(), context)
    assert not passed
    assert 47.0 in unverified
