"""v3.0 Prompt A/B: scoring + winner-selection logic (pure, no LLM)."""

from app.experiments import pick_winner, score_variant


def test_score_variant_rates():
    s = score_variant(faithful=9, grounded=8, total=10, cost_usd=0.05)
    assert s["faithfulness"] == 0.9 and s["groundedness"] == 0.8 and s["n"] == 10


def test_faithfulness_gate_disqualifies_below_100():
    # 'pretty' has higher groundedness but isn't 100% faithful → cannot win.
    scores = {
        "safe":   {"faithfulness": 1.0, "groundedness": 0.70, "cost_usd": 0.05, "n": 10},
        "pretty": {"faithfulness": 0.9, "groundedness": 0.95, "cost_usd": 0.04, "n": 10},
    }
    d = pick_winner(scores)
    assert d["winner"] == "safe" and d["qualified"] == ["safe"]


def test_no_variant_qualifies():
    scores = {
        "a": {"faithfulness": 0.8, "groundedness": 0.9, "cost_usd": 0.05, "n": 10},
        "b": {"faithfulness": 0.9, "groundedness": 0.5, "cost_usd": 0.05, "n": 10},
    }
    d = pick_winner(scores)
    assert d["winner"] is None and d["conclusive"] is False
    assert "100% faithfulness" in d["reason"]


def test_clear_winner_on_groundedness():
    scores = {
        "a": {"faithfulness": 1.0, "groundedness": 0.90, "cost_usd": 0.05, "n": 10},
        "b": {"faithfulness": 1.0, "groundedness": 0.60, "cost_usd": 0.04, "n": 10},
    }
    d = pick_winner(scores)
    assert d["winner"] == "a" and d["conclusive"] is True


def test_inconclusive_when_within_margin():
    scores = {
        "a": {"faithfulness": 1.0, "groundedness": 0.82, "cost_usd": 0.05, "n": 10},
        "b": {"faithfulness": 1.0, "groundedness": 0.80, "cost_usd": 0.04, "n": 10},
    }
    d = pick_winner(scores)
    # 'a' ranks first but the 2pp gap is within the 5% margin → not decisive.
    assert d["winner"] == "a" and d["conclusive"] is False
    assert "inconclusive" in d["reason"].lower()


def test_cost_tiebreak_when_groundedness_equal():
    scores = {
        "cheap": {"faithfulness": 1.0, "groundedness": 0.80, "cost_usd": 0.03, "n": 10},
        "dear":  {"faithfulness": 1.0, "groundedness": 0.80, "cost_usd": 0.09, "n": 10},
    }
    d = pick_winner(scores)
    assert d["ranking"][0] == "cheap"   # equal groundedness → cheaper wins the rank
