"""v5.0 Predictive Narratives: deterministic forecasting + MAPE + endpoint."""

import pytest
from dbharness import use_test_db

from app.compute.forecast import backtest_mape, forecast, next_periods


def test_next_periods_rolls_year():
    assert next_periods("2025-11", 3) == ["2025-12", "2026-01", "2026-02"]


def test_linear_series_forecast_is_accurate():
    # Perfectly linear history → projection continues the line.
    y = [float(x) for x in range(1, 13)]   # 1..12
    proj = forecast(y, 3)
    assert proj == [13.0, 14.0, 15.0]
    assert backtest_mape(y) is not None and backtest_mape(y) < 10.0   # success criterion


def test_seasonal_series_forecast_low_error():
    # 3 years of a clean seasonal + trend pattern.
    base = [100, 110, 130, 120, 140, 150, 145, 155, 160, 150, 140, 135]
    y = [v + 10 * yr for yr in range(3) for v in base]
    mape = backtest_mape(y, season=12)
    assert mape is not None and mape < 10.0


def test_forecast_handles_short_series():
    assert forecast([5.0], 3) == [5.0, 5.0, 5.0]   # last-value fallback
    assert len(forecast([2.0, 4.0, 6.0], 2)) == 2


# ---------------------------------------------------------------- endpoint
@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main
    from app.config import settings

    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "vector_backend", "faiss")
    monkeypatch.setattr(settings, "embedding_provider", "offline")
    monkeypatch.setattr(settings, "supabase_url", "")

    from app.deps import shared_cache, shared_embedder, shared_vector_store
    shared_cache.cache_clear()
    shared_embedder.cache_clear()
    shared_vector_store.cache_clear()

    main.init_db()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def test_forecast_endpoint(client):
    rows = ["period,metric,value,budget"]
    for i in range(1, 13):
        rows.append(f"2025-{i:02d},Signups,{100 + i * 10},{100 + i * 10}")
    uid = client.post("/ingest/upload", files={"file": ("f.csv", "\n".join(rows), "text/csv")}).json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})

    r = client.get("/forecast", params={"metric": "Signups", "horizon": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["period"] for p in body["projections"]] == ["2026-01", "2026-02", "2026-03"]
    assert len(body["projections"]) == 3
    # Rising series → next projection above the last actual (220).
    assert body["projections"][0]["value"] > 220
