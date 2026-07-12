"""Prompt A/B scoring & winner selection (v3.0).

Pure, deterministic logic so it is fully unit-testable without an LLM. The CLI
runner (eval/ab.py) generates narratives with each prompt variant over the golden
set, then hands the aggregated per-variant scores here to pick a winner.

Selection rules, in order:
1. FAITHFULNESS GATE — a variant below 100% numeric faithfulness is disqualified.
   Faithfulness is the product's non-negotiable guarantee; a prettier prompt that
   ever invents a number can never win.
2. Among qualified variants, rank by groundedness (causes traced to context).
3. Tie-break by cost, then by name (stable).
4. INCONCLUSIVE — if the top two qualified variants are within `margin` on
   groundedness, report the ranking but flag it as not statistically decisive
   (the golden set is small; don't crown a winner from noise).
"""

from __future__ import annotations

GROUNDEDNESS_MARGIN = 0.05   # within 5pp on a small golden set → inconclusive


def score_variant(faithful: int, grounded: int, total: int,
                  cost_usd: float = 0.0) -> dict:
    """Aggregate one variant's per-case tallies into rates."""
    return {
        "faithfulness": round(faithful / total, 4) if total else 0.0,
        "groundedness": round(grounded / total, 4) if total else 0.0,
        "cost_usd": round(cost_usd, 6),
        "n": total,
    }


def pick_winner(scores: dict[str, dict], margin: float = GROUNDEDNESS_MARGIN) -> dict:
    """Choose a winner from {variant_name: score_dict}. Returns the decision with
    the winner, whether it's conclusive, and a human-readable reason."""
    if not scores:
        return {"winner": None, "conclusive": False, "reason": "no variants scored",
                "qualified": [], "ranking": []}

    qualified = [name for name, s in scores.items() if s.get("faithfulness", 0) >= 1.0]

    if not qualified:
        # Nobody hits the 100% gate — surface the least-bad for triage, but no winner.
        best = max(scores, key=lambda n: (scores[n]["faithfulness"], scores[n]["groundedness"]))
        return {
            "winner": None, "conclusive": False,
            "reason": (f"no variant reached 100% faithfulness (best: '{best}' at "
                       f"{scores[best]['faithfulness']:.0%}) — none may be promoted"),
            "qualified": [], "ranking": [best],
        }

    # Rank qualified: groundedness desc, then cost asc, then name for stability.
    ranking = sorted(
        qualified,
        key=lambda n: (-scores[n]["groundedness"], scores[n]["cost_usd"], n),
    )
    winner = ranking[0]

    conclusive = True
    reason = f"'{winner}' wins on groundedness ({scores[winner]['groundedness']:.0%}) at 100% faithfulness"
    if len(ranking) > 1:
        runner = ranking[1]
        gap = scores[winner]["groundedness"] - scores[runner]["groundedness"]
        if gap < margin:
            conclusive = False
            reason = (f"'{winner}' edges '{runner}' by {gap:.1%} groundedness — within the "
                      f"{margin:.0%} noise margin, so treat as inconclusive; tie-break was cost")

    return {"winner": winner, "conclusive": conclusive, "reason": reason,
            "qualified": qualified, "ranking": ranking}
