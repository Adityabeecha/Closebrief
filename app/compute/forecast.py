"""Deterministic forecasting for predictive narratives (v5.0).

Pure NumPy (no statsmodels/Prophet — keeps the free-tier image light). Holt-Winters
additive smoothing when there is enough history for it, degrading gracefully to
Holt's linear trend, then simple linear regression, then last-value. The forecast
is computed here; the LLM only phrases forward-looking commentary around these
numbers, so predictive narratives stay inside the "AI never computes" rule.
"""

from __future__ import annotations

import numpy as np

_MONTHS = 12


def next_periods(last_period: str, horizon: int) -> list[str]:
    """Future monthly period labels after `last_period` ('YYYY-MM')."""
    try:
        y, m = (int(x) for x in last_period.split("-")[:2])
    except (ValueError, IndexError):
        return [f"+{i}" for i in range(1, horizon + 1)]
    out = []
    for _ in range(horizon):
        m += 1
        if m > 12:
            m, y = 1, y + 1
        out.append(f"{y:04d}-{m:02d}")
    return out


def _holt_winters(y: np.ndarray, horizon: int, season: int,
                  alpha=0.5, beta=0.1, gamma=0.3) -> list[float]:
    n = len(y)
    level = float(np.mean(y[:season]))
    trend = float((np.mean(y[season:2 * season]) - np.mean(y[:season])) / season)
    seasonal = [float(y[i] - level) for i in range(season)]
    L, T, S = level, trend, list(seasonal)
    for i in range(n):
        s = S[i % season]
        new_l = alpha * (y[i] - s) + (1 - alpha) * (L + T)
        new_t = beta * (new_l - L) + (1 - beta) * T
        S[i % season] = gamma * (y[i] - new_l) + (1 - gamma) * s
        L, T = new_l, new_t
    return [L + h * T + S[(n + h - 1) % season] for h in range(1, horizon + 1)]


def _holt_linear(y: np.ndarray, horizon: int, alpha=0.5, beta=0.3) -> list[float]:
    L, T = float(y[0]), float(y[1] - y[0])
    for i in range(1, len(y)):
        new_l = alpha * y[i] + (1 - alpha) * (L + T)
        T = beta * (new_l - L) + (1 - beta) * T
        L = new_l
    return [L + h * T for h in range(1, horizon + 1)]


def _linear(y: np.ndarray, horizon: int) -> list[float]:
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    return [float(slope * (len(y) + h) + intercept) for h in range(horizon)]


def forecast(values: list[float], horizon: int = 3, season: int = _MONTHS) -> list[float]:
    """Project `horizon` future values, choosing the method by history length."""
    y = np.array([v for v in values if v is not None], dtype=float)
    n = len(y)
    if n == 0:
        return []
    if n >= 2 * season:
        out = _holt_winters(y, horizon, season)
    elif n >= 4:
        out = _holt_linear(y, horizon)
    elif n >= 2:
        out = _linear(y, horizon)
    else:
        out = [float(y[-1])] * horizon
    return [round(v, 2) for v in out]


def backtest_mape(values: list[float], season: int = _MONTHS) -> float | None:
    """Hold out the last few points, forecast them from the rest, return MAPE %
    (success criterion target: within 10%). None if history is too short."""
    y = [v for v in values if v is not None]
    n = len(y)
    k = min(3, max(1, n // 4))
    if n - k < 2:
        return None
    preds = forecast(y[: n - k], k, season)
    actual = y[n - k:]
    errs = [abs(a - p) / abs(a) for a, p in zip(actual, preds) if a != 0]
    return round(100 * float(np.mean(errs)), 2) if errs else None
