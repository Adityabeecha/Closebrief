"""Task 1 proof: two files ingested as two datasets, different KPIs selected in
each; the dashboard endpoint returns exactly the selected KPIs for the ACTIVE
dataset only — never the other dataset's or stale metrics."""

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolated SQLite DB, no Postgres/Redis — patch live settings rather than
    # reloading modules (get_connection/get_cache read settings dynamically).
    import app.main as main
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "vector_backend", "faiss")
    monkeypatch.setattr(settings, "embedding_provider", "offline")
    monkeypatch.setattr(settings, "supabase_url", "")  # auth bypassed for these tests

    from app.deps import shared_cache, shared_embedder, shared_vector_store
    shared_cache.cache_clear()
    shared_embedder.cache_clear()
    shared_vector_store.cache_clear()

    main.init_db()

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def _ingest_csv(client, filename, csv_text):
    r = client.post("/ingest/upload", files={"file": (filename, csv_text, "text/csv")})
    assert r.status_code == 200, r.text
    upload_id = r.json()["upload_id"]
    mapping = {
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget",
    }
    r = client.post(f"/ingest/{upload_id}/mapping", json=mapping)
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


def _select_kpis(client, names):
    kpis = [{"source_metric": n, "display_name": n, "category": "Test",
             "unit": "USD", "direction_good": "up"} for n in names]
    r = client.post("/kpis", json={"kpis": kpis})
    assert r.status_code == 200, r.text


def test_dashboard_scoped_to_active_dataset_selected_kpis(client):
    # Dataset A: metrics Alpha, Beta, Gamma — select only Alpha & Beta.
    csv_a = (
        "period,metric,value,budget\n"
        "2025-01,Alpha,100,90\n2025-02,Alpha,110,95\n"
        "2025-01,Beta,50,55\n2025-02,Beta,60,58\n"
        "2025-01,Gamma,10,10\n2025-02,Gamma,12,11\n"
    )
    ds_a = _ingest_csv(client, "file_a.csv", csv_a)
    _select_kpis(client, ["Alpha", "Beta"])

    # Dataset B: metrics Delta, Epsilon — select only Delta. Now active.
    csv_b = (
        "period,metric,value,budget\n"
        "2025-01,Delta,200,180\n2025-02,Delta,220,200\n"
        "2025-01,Epsilon,5,5\n2025-02,Epsilon,6,6\n"
    )
    _ingest_csv(client, "file_b.csv", csv_b)
    _select_kpis(client, ["Delta"])

    # Active dataset is B: dashboard shows ONLY Delta (selected), not Epsilon,
    # and none of A's Alpha/Beta/Gamma.
    periods = client.get("/periods").json()
    assert periods, "active dataset should have periods"
    facts = client.get(f"/facts?period={periods[-1]}&include_charts=false").json()
    metrics = {f["metric"] for f in facts}
    assert metrics == {"Delta"}, metrics

    # Switch to dataset A: dashboard shows ONLY Alpha & Beta (selected), not Gamma.
    r = client.post(f"/datasets/{ds_a}/activate")
    assert r.status_code == 200
    facts = client.get("/facts?period=2025-02&include_charts=false").json()
    metrics = {f["metric"] for f in facts}
    assert metrics == {"Alpha", "Beta"}, metrics
    assert "Gamma" not in metrics


def test_selected_kpi_without_data_shows_empty_card(client):
    csv = "period,metric,value,budget\n2025-01,Alpha,100,90\n2025-03,Alpha,120,110\n"
    _ingest_csv(client, "f.csv", csv)
    # Select Alpha plus a KPI that has no rows at all.
    _select_kpis(client, ["Alpha", "Zeta"])

    facts = client.get("/facts?period=2025-03&include_charts=false").json()
    by_metric = {f["metric"]: f for f in facts}
    assert set(by_metric) == {"Alpha", "Zeta"}
    assert by_metric["Alpha"]["has_data"] is True
    assert by_metric["Zeta"]["has_data"] is False          # empty card, not hidden
    assert by_metric["Zeta"]["value"] is None


def test_delete_dataset_reactivates_and_scopes(client):
    ds_a = _ingest_csv(client, "a.csv", "period,metric,value,budget\n2025-01,Alpha,1,1\n")
    ds_b = _ingest_csv(client, "b.csv", "period,metric,value,budget\n2025-01,Beta,2,2\n")

    datasets = client.get("/datasets").json()
    assert datasets["active_id"] == ds_b
    assert {d["id"] for d in datasets["datasets"]} == {ds_a, ds_b}

    # Delete active B -> A becomes active; dashboard scoped to A only.
    r = client.delete(f"/datasets/{ds_b}")
    assert r.status_code == 204
    datasets = client.get("/datasets").json()
    assert datasets["active_id"] == ds_a
    facts = client.get("/facts?period=2025-01&include_charts=false").json()
    assert {f["metric"] for f in facts} == {"Alpha"}


def test_default_period_is_latest_in_active_dataset(client):
    # Dataset A covers 2024; dataset B (active) covers only 2025-06..07.
    _ingest_csv(client, "a.csv",
                "period,metric,value,budget\n2024-01,Alpha,1,1\n2024-12,Alpha,2,2\n")
    _ingest_csv(client, "b.csv",
                "period,metric,value,budget\n2025-06,Beta,1,1\n2025-07,Beta,2,2\n")
    periods = client.get("/periods").json()
    assert periods == ["2025-06", "2025-07"]  # B's periods only, not 2024
