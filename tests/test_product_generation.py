"""Regressions from the five SEC dataset analyst run."""

import pytest
from test_datasets import client  # noqa: F401
from test_e2e_analyst_fixes import BUDGET, PL, _note
from test_import_honesty import _import

from app.generation.generate import generate_insight
from app.generation.llm_client import GenerationResult, TokenUsage
from app.schemas import ComputedFact, ContextSnippet, Deltas


class DraftClient:
    def __init__(self, narrative, sources=()):
        self.result = GenerationResult(narrative=narrative, sources_used=list(sources))
        self.prompts = []

    def generate_narrative(self, system, user):
        self.prompts.append((system, user))
        return self.result, TokenUsage()


def fact(**kw):
    return ComputedFact(**{"metric": "Revenue", "period": "2026-10", "value": 80,
                           "deltas": Deltas(budget_var_pct=-20, budget_var_abs=-20), **kw})


@pytest.mark.parametrize("note_kw,expected", [
    ({}, "High"),
    ({"effective_date": None}, "High"),
    ({"effective_date": "2026-09"}, "Medium"),
    ({"metric_tags": []}, "Medium"),
    ({"metric_tags": ["Opex"]}, "Medium"),
    ({"type": "definition", "effective_date": None}, "Medium"),
])
def test_confidence_uses_one_cited_recorded_reason(client, note_kw, expected):  # noqa: F811
    _import(client, PL, BUDGET)
    note = _note(client, **note_kw)
    sid = f"ctx_{note['id']:03d}"
    context = [ContextSnippet(id=sid, title=note["title"], body=note["body"], type=note["type"])]
    result = generate_insight(fact(), context, DraftClient("Revenue was $80.", [sid]),
                              retrieval_scores=[0.01], embedder_semantic=False)
    assert result.confidence == expected


@pytest.mark.parametrize("anomaly,var,expected", [(True, None, "Low"), (False, -11, "Low"),
                                                  (False, 10, "Medium"), (False, None, "Medium")])
def test_uncited_confidence_depends_on_need_for_reason(anomaly, var, expected):
    result = generate_insight(fact(is_anomaly=anomaly, deltas=Deltas(budget_var_pct=var)),
                              [ContextSnippet(id="unused", body="Background")], DraftClient("Revenue was $80."))
    assert result.confidence == expected


def test_invalid_citation_does_not_earn_confidence():
    result = generate_insight(fact(), [], DraftClient("Revenue was $80.", ["missing"]))
    assert result.confidence == "Low" and result.sources == []


def test_uncited_confidence_respects_threshold_on_fact():
    configured = fact().model_copy(update={"reason_threshold_pct": 25})
    assert generate_insight(configured, [], DraftClient("Revenue was $80.")).confidence == "Medium"


def test_failed_figure_check_keeps_confidence_low():
    result = generate_insight(fact(), [], DraftClient("Revenue was $999."))
    assert result.faithfulness == "failed" and result.confidence == "Low"


@pytest.mark.parametrize("closing", ["Watch for continued growth in these areas next month.",
                                    "Watch for further impacts next month.", "Monitor customer growth.",
                                    "Next month, growth should continue."])
def test_uncited_final_watch_sentence_is_removed(closing):
    result = generate_insight(fact(), [], DraftClient(f"Revenue was $80. {closing}"))
    assert result.narrative == "Revenue was $80."


def test_cited_specific_upcoming_event_is_kept():
    context = [ContextSnippet(id="note", body="Renewals expected to sign in November.")]
    text = "Revenue was $80. Watch for renewals expected to sign in November."
    result = generate_insight(fact(), context, DraftClient(text, ["note"]))
    assert result.narrative == text and result.confidence == "Medium"


def test_generation_receives_explicit_cause_and_upcoming_event_rules():
    llm = DraftClient("Revenue was $80.")
    result = generate_insight(fact(), [], llm)
    system = llm.prompts[0][0]
    assert "two facts in the same note" in system.lower()
    assert "for this KPI" in system
    assert "cited note names a specific upcoming event" in system
    assert 'Never say "next month"' in system
    assert result.prompt_version == "v7-close-commentary"


