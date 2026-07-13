"""v4.0 follow-up: configurable data retention purges old telemetry but never
the immutable audit log."""

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


def _conn():
    from app.db import get_connection
    return get_connection()


def test_purge_removes_old_keeps_recent(client):
    from app import retention, workspaces
    conn = _conn()
    try:
        ws = workspaces.create_workspace(conn, "W", "u")
        # One old call (100 days ago) and one recent (today).
        conn.execute(
            "INSERT INTO llm_calls (endpoint, cost_usd, workspace_id, created_at) "
            "VALUES ('/x', 1.0, ?, '2020-01-01 00:00:00')", (ws,))
        conn.execute(
            "INSERT INTO llm_calls (endpoint, cost_usd, workspace_id) VALUES ('/x', 1.0, ?)", (ws,))
        conn.commit()
        res = retention.purge_workspace(conn, ws, retention_days=30)
        assert res["llm_calls_purged"] == 1
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM llm_calls WHERE workspace_id = ?", (ws,)).fetchone()["n"]
        assert remaining == 1
    finally:
        conn.close()


def test_run_retention_only_purges_configured_workspaces(client):
    from app import retention, workspaces
    conn = _conn()
    try:
        keep = workspaces.create_workspace(conn, "NoPolicy", "u1")     # no retention set
        purge = workspaces.create_workspace(conn, "Policy", "u2")
        conn.execute("UPDATE workspaces SET retention_days = 30 WHERE id = ?", (purge,))
        for ws in (keep, purge):
            conn.execute(
                "INSERT INTO llm_calls (endpoint, cost_usd, workspace_id, created_at) "
                "VALUES ('/x', 1.0, ?, '2020-01-01 00:00:00')", (ws,))
        conn.commit()
        retention.run_retention(conn)
        # The policy workspace's old row is gone; the no-policy one is untouched.
        assert conn.execute("SELECT COUNT(*) AS n FROM llm_calls WHERE workspace_id = ?", (purge,)).fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM llm_calls WHERE workspace_id = ?", (keep,)).fetchone()["n"] == 1
    finally:
        conn.close()


def test_retention_endpoint(client):
    ws = client.post("/workspaces", json={"name": "Acme"}).json()["id"]
    r = client.put(f"/workspaces/{ws}/retention", json={"retention_days": 90})
    assert r.status_code == 200 and r.json()["retention_days"] == 90
