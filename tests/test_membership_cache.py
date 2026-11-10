"""Workspace membership cache: request connection counts and immediate invalidation."""

from uuid import uuid4

import pytest
from dbharness import use_test_db
from fastapi.testclient import TestClient


@pytest.fixture
def membership(monkeypatch, tmp_path):
    from app import datasets, db, main, workspaces
    from app.auth import CurrentUser
    from app.config import settings

    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "membership.db"))
    db.init_db()
    clear = getattr(workspaces, "invalidate_membership_cache", lambda *args: None)
    clear()
    first, second = str(uuid4()), str(uuid4())
    conn = db.get_connection()
    try:
        alpha = workspaces.create_workspace(conn, "Alpha", first)
        beta = workspaces.create_workspace(conn, "Beta", second)
    finally:
        conn.close()
    calls = []
    clock = [1000.0]

    def connection():
        calls.append(True)
        return db.get_connection()

    monkeypatch.setattr(main, "get_connection", connection)
    monkeypatch.setattr(main, "auth_active", lambda: True)
    monkeypatch.setattr(main, "authenticate", lambda request: CurrentUser(
        id=request.headers.get("x-test-user", first), email="visitor@example.com", role="analyst"))
    monkeypatch.setattr(workspaces, "monotonic", lambda: clock[0], raising=False)
    ws_token = datasets._workspace_scope.set(None)
    demo_token = datasets._demo_scope.set(False)
    try:
        yield TestClient(main.app), calls, clock, first, second, alpha, beta
    finally:
        clear()
        datasets._workspace_scope.reset(ws_token)
        datasets._demo_scope.reset(demo_token)


def test_second_authenticated_request_does_not_open_a_connection(membership):
    client, calls, *_ = membership
    assert client.get("/me").status_code == 200
    assert len(calls) == 1
    assert client.get("/me").status_code == 200
    assert len(calls) == 1


def test_membership_resolution_expires_after_sixty_seconds(membership):
    client, calls, clock, *_ = membership
    client.get("/me")
    clock[0] += 59
    client.get("/me")
    assert len(calls) == 1
    clock[0] += 2
    client.get("/me")
    assert len(calls) == 2


def test_cache_keys_include_user_and_requested_workspace(membership):
    from app import workspaces
    from app.db import get_connection

    client, calls, _, first, second, _, beta = membership
    conn = get_connection()
    try:
        workspaces.add_member(conn, beta, first, None, "analyst")
    finally:
        conn.close()
    headers = [{}, {"X-Workspace-Id": str(beta)}, {"X-Workspace-Id": str(beta), "X-Test-User": second}]
    for _ in range(2):
        assert [client.get("/me", headers=h).json()["workspace_role"] for h in headers] == ["admin", "analyst", "admin"]
    assert len(calls) == 3


def test_joining_a_workspace_invalidates_the_cached_fallback_immediately(membership):
    from app import workspaces
    from app.db import get_connection

    client, calls, _, _, _, _, beta = membership
    conn = get_connection()
    try:
        invite = workspaces.create_invite(conn, beta, "analyst")
    finally:
        conn.close()
    headers = {"X-Workspace-Id": str(beta)}
    assert client.get("/me", headers=headers).json()["workspace_role"] == "admin"
    assert client.post("/workspaces/join", headers=headers, json={"token": invite}).status_code == 200
    calls.clear()
    assert client.get("/me", headers=headers).json()["workspace_role"] == "analyst"
    assert len(calls) == 1
    client.get("/me", headers=headers)
    assert len(calls) == 1


def test_add_member_role_change_invalidates_cached_resolution(membership):
    from app import workspaces
    from app.db import get_connection

    client, calls, _, first, _, alpha, _ = membership
    assert client.get("/me").json()["workspace_role"] == "admin"
    conn = get_connection()
    try:
        workspaces.add_member(conn, alpha, first, None, "executive")
    finally:
        conn.close()
    assert client.get("/me").json()["workspace_role"] == "executive"
    assert len(calls) == 2


def test_creating_a_workspace_invalidates_the_owners_resolutions(membership):
    from app import workspaces
    from app.db import get_connection

    client, calls, _, first, *_ = membership
    client.get("/me")
    conn = get_connection()
    try:
        workspaces.create_workspace(conn, "New workspace", first)
    finally:
        conn.close()
    client.get("/me")
    assert len(calls) == 2


def test_invalidation_can_target_one_user_or_everyone(membership):
    from app import workspaces

    client, calls, _, first, second, *_ = membership
    client.get("/me")
    client.get("/me", headers={"X-Test-User": second})
    workspaces.invalidate_membership_cache(first)
    client.get("/me", headers={"X-Test-User": second})
    assert len(calls) == 2
    client.get("/me")
    assert len(calls) == 3
    workspaces.invalidate_membership_cache()
    client.get("/me")
    client.get("/me", headers={"X-Test-User": second})
    assert len(calls) == 5


def test_sandbox_purge_invalidates_removed_memberships(membership):
    from app import retention, workspaces
    from app.db import get_connection

    client, calls, _, first, _, alpha, _ = membership
    client.get("/me")
    conn = get_connection()
    try:
        conn.execute("UPDATE workspaces SET created_at = '2000-01-01 00:00:00' WHERE id = ?", (alpha,))
        conn.commit()
        assert retention.purge_inactive_sandboxes(conn)["workspaces_purged"] == 1
    finally:
        conn.close()
    calls.clear()
    assert client.get("/me").status_code == 200
    assert len(calls) == 1
    conn = get_connection()
    try:
        assert workspaces.list_user_workspaces(conn, first)[0]["id"] != alpha
    finally:
        conn.close()


def test_waiting_for_a_pool_connection_does_not_block_membership_invalidation(membership):
    from threading import BoundedSemaphore, Event, Thread

    from app import workspaces
    from app.db import get_connection

    _, _, _, first, _, alpha, _ = membership
    pool = BoundedSemaphore(1)
    pool.acquire()  # A membership mutation owns the only connection.
    checkout_started = Event()

    def commit_membership_and_return_connection():
        checkout_started.wait()
        workspaces.invalidate_membership_cache(first)
        pool.release()

    worker = Thread(target=commit_membership_and_return_connection)
    worker.start()

    class PooledConnection:
        def __init__(self):
            self.conn = get_connection()

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def close(self):
            self.conn.close()
            pool.release()

    def checkout():
        checkout_started.set()
        assert pool.acquire(timeout=2), "Cache lock blocked invalidation and prevented the connection returning to the pool"
        return PooledConnection()

    try:
        assert workspaces.resolve_workspace(None, first, None, None, connection_factory=checkout) == (alpha, "admin")
    finally:
        checkout_started.set()
        worker.join(timeout=3)
        assert not worker.is_alive()
