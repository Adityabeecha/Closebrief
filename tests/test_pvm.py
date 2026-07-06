import pytest

from app.compute.pvm import PVMItem, bridge_for_metric_row, decompose


def test_single_item_reconciles():
    bridge = decompose([PVMItem(actual_qty=110, budget_qty=100, actual_price=9.5, budget_price=10.0)])
    total_variance = 110 * 9.5 - 100 * 10.0  # 45
    assert bridge is not None
    assert bridge.reconciles
    assert bridge.volume + bridge.price + bridge.mix == pytest.approx(total_variance, abs=0.01)
    assert bridge.volume == pytest.approx(10 * 10.0)          # (110-100)*10
    assert bridge.price == pytest.approx(-0.5 * 110)          # (9.5-10)*110


def test_multi_item_mix_captures_composition_shift():
    items = [
        PVMItem(actual_qty=50, budget_qty=100, actual_price=5, budget_price=5),   # cheap product shrank
        PVMItem(actual_qty=150, budget_qty=100, actual_price=20, budget_price=20), # premium grew
    ]
    bridge = decompose(items)
    assert bridge.reconciles
    assert bridge.volume + bridge.price + bridge.mix == pytest.approx(bridge.total, abs=0.01)


def test_missing_detail_returns_none():
    assert bridge_for_metric_row(None, 100, 10, 10) is None
    assert bridge_for_metric_row(100, None, 10, 10) is None
    assert bridge_for_metric_row(100, 100, None, 10) is None
    assert decompose([]) is None


def test_full_detail_returns_bridge():
    bridge = bridge_for_metric_row(110, 100, 9.5, 10.0)
    assert bridge is not None
    assert bridge.reconciles
