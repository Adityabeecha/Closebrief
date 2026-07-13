"""v4.0 usage metering: per-workspace spend, tier budgets, and the pre-flight
spend-limit guard (402 when over budget)."""

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


def _conn():
    from app.db import get_connection
    return get_connection()


def _spend(conn, ws, amount):
    conn.execute(
        "INSERT INTO llm_calls (endpoint, cost_usd, workspace_id) VALUES ('/x', ?, ?)",
        (amount, ws),
    )
    conn.commit()


def test_month_to_date_spend_is_per_workspace(client):
    from app import billing, workspaces
    conn = _conn()
    try:
        a = workspaces.create_workspace(conn, "A", "u-a")
        b = workspaces.create_workspace(conn, "B", "u-b")
        _spend(conn, a, 1.25)
        _spend(conn, a, 0.75)
        _spend(conn, b, 4.0)
        assert billing.month_to_date_spend(conn, a) == 2.0
        assert billing.month_to_date_spend(conn, b) == 4.0
    finally:
        conn.close()


def test_tier_budget_and_over_budget(client):
    from app import billing, workspaces
    conn = _conn()
    try:
        ws = workspaces.create_workspace(conn, "Free co", "u")   # free tier -> $5 default
        assert billing.effective_budget(conn, ws) == 5.0
        _spend(conn, ws, 4.99)
        assert billing.is_over_budget(conn, ws) is False
        _spend(conn, ws, 0.02)
        assert billing.is_over_budget(conn, ws) is True
    finally:
        conn.close()


def test_explicit_budget_overrides_tier(client):
    from app import billing, workspaces
    conn = _conn()
    try:
        ws = workspaces.create_workspace(conn, "Custom", "u")
        conn.execute("UPDATE workspaces SET monthly_budget_usd = 1.0 WHERE id = ?", (ws,))
        conn.commit()
        assert billing.effective_budget(conn, ws) == 1.0
        _spend(conn, ws, 1.0)
        assert billing.is_over_budget(conn, ws) is True
    finally:
        conn.close()


def test_enforce_budget_blocks_llm_when_over(client, monkeypatch):
    import app.main as main
    from app import datasets, workspaces
    conn = _conn()
    try:
        ws = workspaces.create_workspace(conn, "Broke", "u")
        conn.execute("UPDATE workspaces SET monthly_budget_usd = 0.0 WHERE id = ?", (ws,))
        conn.commit()
    finally:
        conn.close()
    # Simulate the request being scoped to that workspace.
    datasets.set_workspace_scope(ws)
    try:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            main._enforce_budget()
        assert ei.value.status_code == 402
    finally:
        datasets.set_workspace_scope(None)


def test_usage_endpoint(client):
    ws_id = client.post("/workspaces", json={"name": "Acme"}).json()["id"]
    r = client.get(f"/workspaces/{ws_id}/usage")
    assert r.status_code == 200
    assert r.json()["plan"] == "free" and r.json()["monthly_budget_usd"] == 5.0
    # Raise the limit via the admin endpoint.
    r2 = client.put(f"/workspaces/{ws_id}/limit", json={"monthly_budget_usd": 100})
    assert r2.status_code == 200 and r2.json()["monthly_budget_usd"] == 100.0
