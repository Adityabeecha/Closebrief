"""Anomaly root-cause drill-down (v5.3): deterministic decomposition of what
moved a metric — z-score vs baseline, correlated movers, trend, and (when
qty/price exists) price/volume/mix — behind /insights/root-cause."""

import pytest
from dbharness import use_test_db


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


def _ingest(client, csv):
    uid = client.post("/ingest/upload", files={"file": ("f.csv", csv, "text/csv")}).json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})


def _rising_csv():
    rows = ["period,metric,value,budget"]
    for m in range(1, 7):
        rows.append(f"2025-{m:02d},Revenue,{100 + m * 10},{100 + m * 10 - 3}")
        rows.append(f"2025-{m:02d},COGS,{40 + m * 4},{41 + m * 4}")
    return "\n".join(rows) + "\n"


def test_root_cause_decomposition(client):
    _ingest(client, _rising_csv())
    rc = client.get("/insights/root-cause?metric=Revenue&period=2025-06").json()
    assert rc["metric"] == "Revenue" and rc["value"] == 160.0
    assert rc["prior_value"] == 150.0
    # z-score computed vs the metric's own trailing baseline.
    assert rc["z_score"] is not None and rc["baseline_mean"] is not None
    # A sustained rising streak is detected.
    assert rc["trend"] and rc["trend"]["direction"] == "growing" and rc["trend"]["months"] >= 3
    # COGS moves with Revenue -> surfaces as a correlated driver.
    assert any(d["metric"] == "COGS" for d in rc["drivers"])
    # No qty/price detail -> no PVM split (graceful), but a primary factor exists.
    assert rc["pvm"] is None
    assert rc["primary_factor"]


def test_root_cause_pvm_when_qty_price_present(client):
    # Layout with quantity + price so the P/V/M bridge is computed.
    csv = ("period,metric,value,budget,quantity,price,budget_quantity,budget_price\n"
           "2025-01,Widgets,100,90,10,10,9,10\n"
           "2025-02,Widgets,144,90,12,12,9,10\n")
    uid = client.post("/ingest/upload", files={"file": ("f.csv", csv, "text/csv")}).json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric", "value_col": "value",
        "budget_col": "budget", "quantity_col": "quantity", "price_col": "price",
        "budget_quantity_col": "budget_quantity", "budget_price_col": "budget_price"})
    rc = client.get("/insights/root-cause?metric=Widgets&period=2025-02").json()
    assert rc["pvm"] is not None
    comps = {c["component"] for c in rc["pvm"]}
    assert comps == {"Volume", "Price", "Mix"}
    # Shares are non-negative and the biggest component leads.
    assert rc["pvm"][0]["share_pct"] >= rc["pvm"][-1]["share_pct"]


def test_root_cause_errors(client):
    _ingest(client, _rising_csv())
    assert client.get("/insights/root-cause?metric=Nope&period=2025-06").status_code == 404
    assert client.get("/insights/root-cause?metric=Revenue").status_code == 422
