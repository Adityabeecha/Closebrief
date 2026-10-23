"""Digest prompts carry executive-rounded figures, never raw floats (QA P0 #4)."""

import re

from app.digest.digest import build_digest_prompt
from app.generation.guard import check_faithfulness
from app.schemas import ComputedFact, Deltas


def _hosting_fact() -> ComputedFact:
    # Values copied from the QA run that produced "rose 1.1999997910746822%".
    return ComputedFact(
        metric="Cloud Hosting & Infrastructure",
        period="2026-06",
        value=271254.84,
        prior_value=268038.38,
        unit="USD",
        deltas=Deltas(
            mom_pct=1.1999997910746822,
            yoy_pct=37.59,
            budget_var_abs=-245.16,
            budget_var_pct=-0.09029834254144847,
        ),
        trend="flat",
        is_anomaly=False,
    )


def test_digest_prompt_has_no_long_floats():
    prompt = build_digest_prompt(_hosting_fact())
    assert not re.search(r"\d\.\d{3,}", prompt), prompt
    assert "$271.3K" in prompt
    assert "+1.2%" in prompt
    assert "-$245" in prompt
    assert "-0.1%" in prompt


def test_rounded_digest_text_still_passes_guard():
    fact = _hosting_fact()
    text = (
        "Cloud Hosting & Infrastructure rose +1.2% to $271.3K.\n"
        "It finished -$245 versus budget, a -0.1% variance."
    )
    passed, unverified = check_faithfulness(text, fact)
    assert passed, unverified


def test_non_currency_units_are_not_dollarised():
    fact = ComputedFact(
        metric="MQLs", period="2026-06", value=279.0, prior_value=264.0, unit="count",
        deltas=Deltas(mom_pct=5.68, budget_var_abs=-21.0, budget_var_pct=-7.0),
        trend="up", is_anomaly=False,
    )
    prompt = build_digest_prompt(fact)
    assert "$" not in prompt
    assert "value: 279" in prompt
