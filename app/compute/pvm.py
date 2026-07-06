"""Price / Volume / Mix decomposition (Addendum v1.1 Section 5).

Deterministic only. Given actual & budget quantity and price — optionally per
sub-item (dimension) — decompose a revenue-type variance into:

    volume = (actual_qty - budget_qty) * budget_price
    price  = (actual_price - budget_price) * actual_qty
    mix    = residual from composition shifts across sub-items

Reconciliation must hold: volume + price + mix == total variance (asserted
within tolerance). With no quantity/price detail, callers get None — never a
fabricated decomposition.
"""

from pydantic import BaseModel


class PVMItem(BaseModel):
    actual_qty: float
    budget_qty: float
    actual_price: float
    budget_price: float


class VarianceBridge(BaseModel):
    volume: float
    price: float
    mix: float
    total: float
    reconciles: bool


def decompose(items: list[PVMItem], tolerance: float = 0.01) -> VarianceBridge | None:
    """Decompose across one or more sub-items. For a single item, mix is the
    qty*price cross-term; across items, mix additionally captures composition
    shift. Returns None for empty input."""
    if not items:
        return None

    total_actual = sum(i.actual_qty * i.actual_price for i in items)
    total_budget = sum(i.budget_qty * i.budget_price for i in items)
    total_variance = total_actual - total_budget

    volume = sum((i.actual_qty - i.budget_qty) * i.budget_price for i in items)
    price = sum((i.actual_price - i.budget_price) * i.actual_qty for i in items)
    # Everything not explained by pure volume (at budget price) or pure price
    # (at actual qty) is mix: the qty x price interaction + composition shift.
    mix = total_variance - volume - price

    reconciles = abs((volume + price + mix) - total_variance) <= max(
        tolerance, abs(total_variance) * 1e-9
    )
    return VarianceBridge(
        volume=round(volume, 2),
        price=round(price, 2),
        mix=round(mix, 2),
        total=round(total_variance, 2),
        reconciles=reconciles,
    )


def bridge_for_metric_row(
    actual_qty, budget_qty, actual_price, budget_price
) -> VarianceBridge | None:
    """Bridge from a single metric_values row; None when any input is missing
    (graceful degradation — the narrative must not claim P/V/M causes)."""
    values = (actual_qty, budget_qty, actual_price, budget_price)
    if any(v is None for v in values):
        return None
    return decompose(
        [
            PVMItem(
                actual_qty=float(actual_qty),
                budget_qty=float(budget_qty),
                actual_price=float(actual_price),
                budget_price=float(budget_price),
            )
        ]
    )
