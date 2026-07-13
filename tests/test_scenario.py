"""v5.0 Scenario Modeling: deterministic price/volume/mix what-if."""

import time

import pytest

from app.compute.scenario import run_scenario


def test_price_and_volume_compound():
    r = run_scenario(1000.0, budget=1100.0, price_pct=10, volume_pct=10)
    assert r["projected_value"] == 1210.0        # 1000 * 1.1 * 1.1
    assert r["impact_abs"] == 210.0 and r["impact_pct"] == 21.0
    assert r["vs_budget"] == 110.0               # 1210 - 1100


def test_negative_lever_and_no_budget():
    r = run_scenario(500.0, budget=None, volume_pct=-20)
    assert r["projected_value"] == 400.0
    assert r["vs_budget"] is None and r["vs_budget_pct"] is None


def test_scenario_is_fast():
    t0 = time.perf_counter()
    for _ in range(1000):
        run_scenario(1234.5, 1200.0, price_pct=3, volume_pct=-1, mix_pct=2)
    assert (time.perf_counter() - t0) < 3.0      # success criterion, with huge margin


# ---------------------------------------------------------------- endpoint
@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
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


def test_scenario_endpoint(client):
    csv = "period,metric,value,budget\n2025-03,Net Revenue,1000,1100\n"
    uid = client.post("/ingest/upload", files={"file": ("f.csv", csv, "text/csv")}).json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})

    r = client.post("/scenario", json={"metric": "Net Revenue", "price_pct": 10})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["base_value"] == 1000.0 and b["projected_value"] == 1100.0
    assert b["vs_budget"] == 0.0                 # 1100 projected vs 1100 budget
    assert client.post("/scenario", json={"metric": "Nope"}).status_code == 404
