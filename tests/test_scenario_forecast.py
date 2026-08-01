import math

import pytest

from app.compute.forecast import backtest_mape, forecast
from app.ingestion.mapping import coerce_value

REVENUE_18 = [
    2354000.0, 2381000.0, 2437000.0, 2472000.0, 2531000.0, 2601000.0,
    2648000.0, 2694000.0, 2760000.0, 2807000.0, 2851000.0, 2902000.0,
    2951000.0, 3010000.0, 3068000.0, 3121000.0, 3180000.0, 3241000.0,
]


def test_eighteen_points_backtests_within_target():
    mape = backtest_mape(REVENUE_18)
    assert mape is not None
    assert mape < 10.0


def test_eighteen_points_is_below_the_seasonal_threshold():
    from app.compute.forecast import _MONTHS
    assert len(REVENUE_18) < 2 * _MONTHS

    proj = forecast(REVENUE_18, 3)
    assert len(proj) == 3
    assert all(math.isfinite(v) for v in proj)
    assert proj[0] > REVENUE_18[-1]


def test_a_missing_month_does_not_break_the_forecast():
    holed = REVENUE_18[:6] + [None] + REVENUE_18[7:]

    proj = forecast(holed, 3)
    assert len(proj) == 3
    assert all(math.isfinite(v) for v in proj)

    mape = backtest_mape(holed)
    assert mape is not None and mape < 10.0


def test_nan_never_survives_ingestion_into_a_series():
    for raw in ("", "nan", "NaN", "  ", None, float("nan")):
        assert coerce_value(raw) is None


@pytest.mark.parametrize("horizon", [1, 3, 6])
def test_forecast_horizon_is_respected(horizon):
    proj = forecast(REVENUE_18, horizon)
    assert len(proj) == horizon
    assert all(math.isfinite(v) for v in proj)
