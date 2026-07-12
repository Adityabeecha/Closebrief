"""Phase 3: conversation memory in the QA prompt builder."""

from app.generation.prompts import build_qa_prompt
from app.schemas import ComputedFact, Deltas


def _fact():
    return ComputedFact(
        metric="Net Revenue", category="Revenue", period="2025-03", value=5_330_000.0,
        unit="USD", prior_value=4_180_000.0,
        deltas=Deltas(mom_pct=27.6, yoy_pct=None, budget_var_abs=600000.0, budget_var_pct=12.7),
        trend=None, is_anomaly=False,
    )


def test_prompt_without_history_is_unchanged_shape():
    p = build_qa_prompt(_fact(), [], "Why did it rise?")
    assert "Net Revenue" in p and "Why did it rise?" in p
    assert "Conversation so far" not in p


def test_prompt_includes_recent_history():
    history = [
        {"question": "Why did revenue rise?", "answer": "A March pricing change lifted it."},
        {"question": "By how much vs plan?", "answer": "About $600K ahead of budget."},
    ]
    p = build_qa_prompt(_fact(), [], "And versus last year?", history)
    assert "Conversation so far" in p
    assert "Why did revenue rise?" in p and "pricing change" in p
    assert "And versus last year?" in p


def test_history_is_capped_and_truncated():
    history = [{"question": f"q{i}", "answer": "x" * 2000} for i in range(20)]
    p = build_qa_prompt(_fact(), [], "next?", history)
    # Only the last 6 turns are kept (q14..q19)...
    assert "q19" in p and "q14" in p and "q13" not in p
    # ...and each answer is truncated (never the full 2000 chars).
    assert "x" * 601 not in p
