"""Demo mode (v2.9): anonymous read-only access + idempotent sample seeding."""

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "demo.db"))
    monkeypatch.setattr(settings, "vector_backend", "faiss")
    monkeypatch.setattr(settings, "embedding_provider", "offline")
    # Auth ACTIVE (so anonymous is normally 401) + demo mode on.
    monkeypatch.setattr(settings, "supabase_url", "https://demo.supabase.co")
    monkeypatch.setattr(settings, "supabase_jwt_secret", "x" * 32)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", True)

    from app.deps import shared_cache, shared_embedder, shared_vector_store
    shared_cache.cache_clear()
    shared_embedder.cache_clear()
    shared_vector_store.cache_clear()

    main.init_db()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def test_demo_allows_anonymous_reads(client):
    r = client.get("/facts?period=2025-01")
    assert r.status_code == 200


def test_demo_blocks_anonymous_writes(client):
    r = client.post("/context", json={
        "type": "event_note", "title": "x", "body": "y",
        "metric_tags": [], "effective_date": "2025-01",
    })
    # Writes are not in the demo allowlist: the auth gate rejects them outright.
    assert r.status_code == 401


def test_auth_config_exposes_demo_flag(client):
    assert client.get("/auth/config").json()["demo_enabled"] is True


def test_seed_demo_idempotent(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "seed.db"))
    import app.db as db
    db.init_db()
    conn = db.get_connection()
    from app.demo import DEMO_DATASET_NAME, seed_demo
    assert seed_demo(conn) is True          # first run seeds
    assert seed_demo(conn) is False         # second run is a no-op
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM datasets WHERE name = ?", (DEMO_DATASET_NAME,)
    ).fetchone()["n"]
    assert n == 1
    # Facts were computed and KPIs pre-selected
    ds = conn.execute("SELECT id FROM datasets WHERE name = ?", (DEMO_DATASET_NAME,)).fetchone()["id"]
    facts = conn.execute(
        "SELECT COUNT(*) AS n FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id WHERE m.dataset_id = ?",
        (ds,),
    ).fetchone()["n"]
    kpis = conn.execute("SELECT COUNT(*) AS n FROM kpi_configs WHERE dataset_id = ?", (ds,)).fetchone()["n"]
    conn.close()
    assert facts > 0 and kpis > 0
