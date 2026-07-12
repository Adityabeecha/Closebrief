"""v4.0 multi-tenancy: workspace membership, invites, and — most importantly —
cross-tenant data isolation (a member of workspace A cannot see B's data)."""

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


# ---------------------------------------------------------- data isolation
def test_scope_isolates_datasets_between_workspaces(client):
    """The core security property: with workspace scope set to A, B's datasets
    are invisible — enforced in SQL via _scope_pred, not by hiding UI."""
    from app import datasets, workspaces
    conn = _conn()
    try:
        wa = workspaces.create_workspace(conn, "Alpha", "user-a", "a@x.com")
        wb = workspaces.create_workspace(conn, "Beta", "user-b", "b@x.com")

        datasets.set_workspace_scope(wa)
        datasets.create_dataset(conn, "Alpha data", activate=True)
        datasets.set_workspace_scope(wb)
        datasets.create_dataset(conn, "Beta data", activate=True)

        # Scoped to A → only Alpha's dataset is visible.
        datasets.set_workspace_scope(wa)
        names_a = {d["name"] for d in datasets.list_datasets(conn)}
        assert names_a == {"Alpha data"}

        # Scoped to B → only Beta's.
        datasets.set_workspace_scope(wb)
        names_b = {d["name"] for d in datasets.list_datasets(conn)}
        assert names_b == {"Beta data"}
    finally:
        datasets.set_workspace_scope(None)
        conn.close()


def test_active_dataset_is_workspace_scoped(client):
    from app import datasets, workspaces
    conn = _conn()
    try:
        wa = workspaces.create_workspace(conn, "Alpha", "user-a")
        wb = workspaces.create_workspace(conn, "Beta", "user-b")
        datasets.set_workspace_scope(wa)
        datasets.create_dataset(conn, "A ds", activate=True)
        a_active = datasets.active_dataset_id(conn)
        datasets.set_workspace_scope(wb)
        # B has no dataset → its active resolves to None, never A's.
        assert datasets.active_dataset_id(conn) is None
        datasets.set_workspace_scope(wa)
        assert datasets.active_dataset_id(conn) == a_active
    finally:
        datasets.set_workspace_scope(None)
        conn.close()


# ---------------------------------------------------------- membership / invites
def test_membership_and_invite_flow(client):
    from app import workspaces
    conn = _conn()
    try:
        ws = workspaces.create_workspace(conn, "Team", "owner", "owner@x.com")
        assert workspaces.member_role(conn, ws, "owner") == "admin"
        assert workspaces.is_member(conn, ws, "stranger") is False

        token = workspaces.create_invite(conn, ws, "analyst", "new@x.com")
        joined = workspaces.accept_invite(conn, token, "new-user", "new@x.com")
        assert joined == ws
        assert workspaces.member_role(conn, ws, "new-user") == "analyst"
        # An invite is single-use.
        assert workspaces.accept_invite(conn, token, "another", "another@x.com") is None
    finally:
        conn.close()


def test_ensure_user_workspace_provisions_on_first_login(client):
    from app import workspaces
    conn = _conn()
    try:
        assert workspaces.list_user_workspaces(conn, "fresh") == []
        ws = workspaces.ensure_user_workspace(conn, "fresh", "fresh@corp.com")
        again = workspaces.ensure_user_workspace(conn, "fresh", "fresh@corp.com")
        assert ws == again   # idempotent, doesn't create a second
        assert workspaces.member_role(conn, ws, "fresh") == "admin"
    finally:
        conn.close()


# ---------------------------------------------------------- endpoints
def test_workspace_endpoints(client):
    r = client.post("/workspaces", json={"name": "Acme"})
    assert r.status_code == 201, r.text
    ws_id = r.json()["id"]
    assert any(w["id"] == ws_id for w in client.get("/workspaces").json()["workspaces"])
    assert client.post("/workspaces", json={"name": ""}).status_code == 422

    inv = client.post(f"/workspaces/{ws_id}/invites", json={"role": "analyst"})
    assert inv.status_code == 201 and inv.json()["token"]
    assert client.post("/workspaces/join", json={"token": inv.json()["token"]}).status_code == 200
    assert client.post("/workspaces/join", json={"token": "nope"}).status_code == 400
