"""What-if scenario modeling (v5.0). Adjust price / volume / mix levers and see
the projected value and impact — deterministic and instant (pure arithmetic, no
LLM, well under the 3s target). The LLM only phrases the impact afterward.

Levers are percentages (e.g. price=5 means +5%). Price and volume compound
multiplicatively (revenue ≈ price × volume); mix is a further adjustment.
"""

from __future__ import annotations


def run_scenario(base_value: float, budget: float | None, *,
                 price_pct: float = 0.0, volume_pct: float = 0.0,
                 mix_pct: float = 0.0) -> dict:
    factor = (1 + price_pct / 100) * (1 + volume_pct / 100) * (1 + mix_pct / 100)
    projected = base_value * factor
    impact_abs = projected - base_value
    impact_pct = (impact_abs / base_value * 100) if base_value else None
    vs_budget = (projected - budget) if budget is not None else None
    vs_budget_pct = (vs_budget / budget * 100) if budget else None
    return {
        "base_value": round(base_value, 2),
        "projected_value": round(projected, 2),
        "impact_abs": round(impact_abs, 2),
        "impact_pct": round(impact_pct, 2) if impact_pct is not None else None,
        "vs_budget": round(vs_budget, 2) if vs_budget is not None else None,
        "vs_budget_pct": round(vs_budget_pct, 2) if vs_budget_pct is not None else None,
        "levers": {"price_pct": price_pct, "volume_pct": volume_pct, "mix_pct": mix_pct},
    }
