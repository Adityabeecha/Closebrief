"""Commentary fixes from the live test: rates change by points, no filler or note
titles, no repeated streak, and drafts may name the part that drove a variance."""

from app.generation.generate import _without_context_closing
from app.generation.guard import check_faithfulness
from app.generation.prompts import SYSTEM_PROMPT, build_user_prompt
from app.schemas import ComputedFact, Deltas, TrendStreak


def _fact(**kw):
    base = dict(metric="Operating Expenses", period="2026-03", value=1397549.78, unit="USD", prior_value=1287076.96,
                deltas=Deltas(mom_pct=8.58, yoy_pct=16.5, budget_var_abs=42669.78, budget_var_pct=3.15))
    base.update(kw)
    return ComputedFact(**base)


def test_prompt_has_no_filler_or_note_titles_rule():
    assert "No reason recorded yet" not in SYSTEM_PROMPT
    assert "Name the note" not in SYSTEM_PROMPT
    assert "never mention notes, note titles" in SYSTEM_PROMPT


def test_rate_year_on_year_closing_is_in_points():
    gm = _fact(metric="Gross Margin %", value=70.2, unit="%", prior_value=72.2,
               deltas=Deltas(mom_pct=-2.77, yoy_pct=-4.2, budget_var_abs=-1.8, budget_var_pct=-2.5))
    text = _without_context_closing("Gross Margin % was 70.2%, 1.8 points below plan.", gm, None)
    assert text.endswith("It is 3.1 points lower than a year ago.") and "4.2%" not in text


def test_a_streak_already_described_is_not_repeated():
    streak = TrendStreak(metric="Operating Expenses", direction="growing", months=11,
                         start_period="2025-05", end_period="2026-03")
    text = "OpEx was above plan. The trend has been up for 11 months."
    assert _without_context_closing(text, _fact(), streak) == text


def test_contributors_reach_the_prompt_and_pass_the_figure_check():
    fact = _fact(contributor_dimension="department", contributors=[
        {"member": "Sales", "value": 540199.44, "budget": 478880.0, "var_abs": 61319.44, "var_pct": 12.8},
        {"member": "R&D", "value": 584036.74, "budget": 607360.0, "var_abs": -23323.26, "var_pct": -3.84}])
    prompt = build_user_prompt(fact, [])
    assert "contributors by department" in prompt and "Sales" in prompt
    passed, bad = check_faithfulness(
        "Operating Expenses was $1.40M, $42,670 above plan. Sales drove it, $61,319 (+12.8%) over plan, "
        "while R&D came in $23,323 under.", fact)
    assert passed, bad
