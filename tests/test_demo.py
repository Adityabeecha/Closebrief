"""Demo mode (v3.0): isolated read-only workspace. Demo sessions authenticate
as a viewer via the X-Closebrief-Demo header, can only read, and can never see
or touch real datasets."""

import io

import pytest

DEMO = {"X-Closebrief-Demo": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "demo.db"))
    monkeypatch.setattr(settings, "vector_backend", "faiss")
    monkeypatch.setattr(settings, "embedding_provider", "offline")
    # Auth ACTIVE (anonymous is 401) + demo mode on.
    monkeypatch.setattr(settings, "supabase_url", "https://demo.supabase.co")
    monkeypatch.setattr(settings, "supabase_jwt_secret", "x" * 32)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", True)

    from app.deps import shared_cache, shared_embedder, shared_vector_store
    shared_cache.cache_clear()
    shared_embedder.cache_clear()
    shared_vector_store.cache_clear()

    main.init_db()
    # A "real" dataset the demo must never see, plus the seeded demo dataset.
    conn = main.get_connection()
    from app.datasets import create_dataset
    create_dataset(conn, "REAL secret upload", is_demo=False)
    conn.close()
    from app.demo import seed_demo
    conn = main.get_connection()
    seed_demo(conn)
    conn.close()

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def test_demo_sees_only_demo_datasets(client):
    r = client.get("/datasets", headers=DEMO)
    assert r.status_code == 200
    names = {d["name"] for d in r.json()["datasets"]}
    assert names == {"Demo — Sample FP&A", "Demo — Marketing Funnel"}
    assert "REAL secret upload" not in names


def test_demo_can_switch_datasets_and_see_funnel(client):
    # A viewer may switch between demo datasets (read-only navigation)...
    ds = client.get("/datasets", headers=DEMO).json()["datasets"]
    mkt = next(d for d in ds if d["name"] == "Demo — Marketing Funnel")
    assert client.post(f"/datasets/{mkt['id']}/activate", headers=DEMO).status_code == 200
    # ...and the marketing dataset renders the acquisition funnel.
    dom = client.get("/domain", headers=DEMO).json()
    assert dom["slug"] == "marketing" and dom["funnel"]
    periods = client.get("/periods", headers=DEMO).json()
    r = client.get(f"/funnel?period={periods[-1]}", headers=DEMO)
    assert r.status_code == 200
    stages = [s["name"] for s in r.json()["stages"]]
    assert stages == ["Impressions", "Clicks", "Signups", "Conversions"]


def test_demo_cannot_activate_real_dataset(client):
    # The scoped activate check blocks a viewer from flipping is_active on a real
    # dataset (id 1 is 'REAL secret upload' created before seeding).
    assert client.post("/datasets/1/activate", headers=DEMO).status_code == 404


def test_demo_writes_are_forbidden(client):
    # Every write path the visitor might hit must 403 (role guard), not 200.
    assert client.post("/context", headers=DEMO, json={
        "type": "event_note", "title": "x", "body": "y",
        "metric_tags": [], "effective_date": "2025-01"}).status_code == 403
    assert client.request("DELETE", "/datasets/1", headers=DEMO).status_code == 403
    assert client.post(
        "/ingest", headers=DEMO,
        files={"file": ("x.csv", io.BytesIO(b"period,metric,value,budget\n"), "text/csv")},
    ).status_code == 403
    assert client.post("/kpis", headers=DEMO, json={"kpis": []}).status_code == 403


def test_demo_can_read_and_ask(client):
    assert client.get("/periods", headers=DEMO).status_code == 200
    assert client.get("/facts?period=2025-01", headers=DEMO).status_code == 200
    assert client.get("/context", headers=DEMO).status_code == 200


def test_no_demo_header_still_401(client):
    # Without the demo opt-in header, an anonymous request is rejected.
    assert client.get("/facts?period=2025-01").status_code == 401


def test_seed_demo_idempotent(client):
    import app.main as main
    from app.demo import DEMO_DATASET_NAME, seed_demo
    conn = main.get_connection()
    assert seed_demo(conn) is False  # both already seeded by the fixture
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM datasets WHERE name = ?", (DEMO_DATASET_NAME,)
    ).fetchone()["n"]
    conn.close()
    assert n == 1
