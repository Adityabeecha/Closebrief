"""Phase 1: funnel stage-over-stage compute + the /funnel endpoint."""

import pytest


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


_CSV = """period,metric,value,budget
2025-01,Impressions,8000,8000
2025-01,Clicks,480,480
2025-01,Signups,96,96
2025-01,Conversions,20,20
2025-02,Impressions,10000,9000
2025-02,Clicks,500,540
2025-02,Signups,100,108
2025-02,Conversions,25,22
"""


def _setup_marketing(client):
    r = client.post("/ingest/upload", files={"file": ("f.csv", _CSV, "text/csv")})
    assert r.status_code == 200, r.text
    uid = r.json()["upload_id"]
    r = client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})
    assert r.status_code == 200, r.text
    assert client.put("/domain", json={"domain": "marketing"}).status_code == 200


def test_funnel_conversions_and_biggest_leak(client):
    _setup_marketing(client)
    r = client.get("/funnel", params={"period": "2025-02"})
    assert r.status_code == 200, r.text
    f = r.json()
    stages = {s["name"]: s for s in f["stages"]}
    assert [s["name"] for s in f["stages"]] == ["Impressions", "Clicks", "Signups", "Conversions"]
    # Clicks/Impressions = 500/10000 = 5%
    assert stages["Clicks"]["conversion_from_prev"] == 5.0
    assert stages["Signups"]["conversion_from_prev"] == 20.0
    assert stages["Conversions"]["conversion_from_prev"] == 25.0
    assert stages["Clicks"]["drop_off"] == 9500.0
    # Lowest stage-over-stage conversion (5%) is into Clicks.
    assert f["biggest_dropoff_stage"] == "Clicks"
    assert f["overall_conversion"] == 0.25   # 25/10000
    assert f["prior_period"] == "2025-01"


def test_funnel_stage_over_stage_change_vs_prior(client):
    _setup_marketing(client)
    f = client.get("/funnel", params={"period": "2025-02"}).json()
    stages = {s["name"]: s for s in f["stages"]}
    # Clicks conversion 6.0% (Jan) -> 5.0% (Feb) = -1.0pp.
    assert stages["Clicks"]["conversion_mom_pp"] == -1.0
    # Signups conversion held at 20% both months.
    assert stages["Signups"]["conversion_mom_pp"] == 0.0


def test_funnel_404ish_for_nonfunnel_domain(client):
    # Default fpa domain has no funnel -> 400 with a clear message.
    r = client.post("/ingest/upload", files={"file": ("f.csv", _CSV, "text/csv")})
    uid = r.json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})
    r = client.get("/funnel", params={"period": "2025-02"})
    assert r.status_code == 400
    assert "funnel" in r.json()["detail"].lower()
