"""Postgres-only RLS regressions; use CI's TEST_DATABASE_URL, never the dev DB."""

import contextvars
import time
from uuid import uuid4

import jwt
import pytest
from dbharness import TEST_DATABASE_URL, use_test_db
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL (Postgres only)")
SECRET = "offline-rls-test-secret-at-least-32-bytes"


@pytest.fixture
def pg(monkeypatch):
    from app import auth, datasets, db
    from app.config import settings

    scope = datasets._workspace_scope.set(None)
    monkeypatch.setattr(settings, "rls_enabled", True)
    use_test_db(monkeypatch)
    auth.invalidate_role_cache()
    try:
        yield db
    finally:
        datasets._workspace_scope.reset(scope)
        auth.invalidate_role_cache()


def test_all_workspace_columns_have_forced_rls_and_policy(pg):
    conn = pg.get_connection()
    try:
        rows = conn.execute("""
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   p.qual, p.with_check
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'workspace_id'
            LEFT JOIN pg_policies p ON p.schemaname = n.nspname
                AND p.tablename = c.relname AND p.policyname = 'ws_isolation'
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT a.attisdropped
        """).fetchall()
        assert rows
        for row in rows:
            assert row["relrowsecurity"] and row["relforcerowsecurity"], row["relname"]
            assert "app.workspace_id" in (row["qual"] or ""), row["relname"]
            assert "app.workspace_id" in (row["with_check"] or ""), row["relname"]
    finally:
        conn.close()


@pytest.mark.parametrize("role", ["admin", "analyst", "executive"])
def test_authenticated_endpoint_queries_always_have_workspace_guc(pg, monkeypatch, role):
    """Exercise cold/warm bootstrap, alternating tenants and an untrusted header.

    Authentication and verified-membership discovery are the sole request
    bootstrap exception: the workspace cannot be known before those reads.
    Demo/anonymous viewer sessions use the separate public demo universe.
    """
    from app import auth, datasets, main, workspaces
    from app.config import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "supabase_url", "https://offline.invalid")
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
    users = [str(uuid4()), str(uuid4())]
    conn = pg.get_connection()
    try:
        for uid in users:
            conn.execute("INSERT INTO app_roles (user_id, email, role) VALUES (?, ?, ?)",
                         (uid, f"{uid}@example.test", role))
        conn.commit()
    finally:
        conn.close()

    bootstrap = contextvars.ContextVar("bootstrap", default=False)
    for name in ("authenticate", "resolve_workspace"):
        original = getattr(main, name)

        def wrap(*args, _original=original, **kwargs):
            token = bootstrap.set(True)
            try:
                return _original(*args, **kwargs)
            finally:
                bootstrap.reset(token)

        monkeypatch.setattr(main, name, wrap)

    execute = pg.PostgresConnection.execute
    observed = []

    def checked_execute(self, sql, params=()):
        guc = self._raw.execute("SELECT current_setting('app.workspace_id', true) AS ws").fetchone()["ws"]
        if bootstrap.get():
            # Bootstrap may only touch identity/membership tables.
            assert any(t in sql for t in ("app_roles", "workspace_members", "workspaces")), sql
        else:
            assert datasets.current_workspace() is not None, sql
            assert guc == str(datasets.current_workspace()), (sql, guc)
            observed.append(guc)
        return execute(self, sql, params)

    monkeypatch.setattr(pg.PostgresConnection, "execute", checked_execute)
    client = TestClient(main.app)  # no lifespan/background work in the request audit
    workspaces_seen = {}
    for _ in range(2):  # first pass provisions, second uses membership/role caches
        for uid in users:
            token = jwt.encode({"sub": uid, "email": f"{uid}@example.test", "aud": "authenticated",
                                "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256")
            headers = {"Authorization": f"Bearer {token}", "X-Workspace-Id": "2147483647"}
            for path in ("/datasets", "/facts?period=2025-01", "/kpis", "/domain", "/workspaces"):
                before = len(observed)
                response = client.get(path, headers=headers)
                assert response.status_code == 200, response.text
                assert len(observed) > before, path
                workspaces_seen.setdefault(uid, observed[-1])
                assert observed[-1] == workspaces_seen[uid]
    assert len(set(workspaces_seen.values())) == 2
    # Force both bootstrap caches cold once more; scope must still be installed.
    auth.invalidate_role_cache()
    workspaces.invalidate_membership_cache()
    assert client.get("/datasets", headers=headers).status_code == 200


@pytest.fixture
def non_bypass_role(pg):
    """CI's schema owner creates a temporary role so tests cannot bypass RLS."""
    role = f"rls_spend_{uuid4().hex}"
    conn = pg.get_connection()
    try:
        conn.execute(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS")
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        conn.execute(f"GRANT SELECT, INSERT ON llm_calls TO {role}")
        conn.execute(f"GRANT SELECT, INSERT, UPDATE ON ai_spend_daily TO {role}")
        conn.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
        conn.commit()
        yield role
    finally:
        conn.rollback()
        conn.execute(f"DROP OWNED BY {role}")
        conn.execute(f"DROP ROLE {role}")
        conn.commit()
        conn.close()


def test_global_counter_counts_two_tenants_while_connections_stay_scoped(pg, non_bypass_role, monkeypatch):
    from fastapi import HTTPException

    from app import datasets, services, workspaces
    from app.auth import CurrentUser
    from app.config import settings

    monkeypatch.setattr(settings, "global_daily_ai_budget_usd", 5.0)
    monkeypatch.setattr(settings, "user_daily_ai_budget_usd", 0.5)
    users = [CurrentUser(id=str(uuid4()), email=f"{i}@example.test", role="analyst") for i in range(2)]
    owner = pg.get_connection()
    try:
        workspaces_ids = [workspaces.create_workspace(owner, f"Tenant {i}", user.id) for i, user in enumerate(users)]
    finally:
        owner.close()

    def scoped_connection():
        conn = pg.get_connection()
        # Transaction-local role resets on commit/rollback; pool state stays clean.
        conn.execute(f"SET LOCAL ROLE {non_bypass_role}")
        return conn

    monkeypatch.setattr(services, "get_connection", scoped_connection)
    scope = datasets._workspace_scope.set(None)
    try:
        for ws, user, amount in zip(workspaces_ids, users, (0.30, 4.70)):
            datasets.set_workspace_scope(ws)
            services.log_llm_call("/offline-test", None, 0, 0, amount, 0, user.id)
        for ws, user, own_spend in zip(workspaces_ids, users, (0.30, 4.70)):
            datasets.set_workspace_scope(ws)
            conn = scoped_connection()
            try:
                before = conn.execute("SELECT current_setting('app.workspace_id') AS ws").fetchone()["ws"]
                assert before == str(ws)
                # Real RLS hides the other tenant's detailed rows.
                assert float(conn.execute("SELECT SUM(cost_usd) AS s FROM llm_calls").fetchone()["s"]) == pytest.approx(own_spend)
                assert services._spent_today(conn) == pytest.approx(5.0)
                with pytest.raises(HTTPException) as error:
                    services.enforce_daily_caps(conn, user)
                assert error.value.status_code == 429 and "for this demo" in error.value.detail
                assert conn.execute("SELECT current_setting('app.workspace_id') AS ws").fetchone()["ws"] == before
                # Disabling the global cap leaves the original per-user cap.
                monkeypatch.setattr(settings, "global_daily_ai_budget_usd", 0)
                if own_spend < 0.5:
                    services.enforce_daily_caps(conn, user)
                else:
                    with pytest.raises(HTTPException) as error:
                        services.enforce_daily_caps(conn, user)
                    assert "You've used" in error.value.detail
                monkeypatch.setattr(settings, "global_daily_ai_budget_usd", 5.0)
            finally:
                conn.close()
    finally:
        datasets._workspace_scope.reset(scope)


def test_postgres_daily_counter_backfills_only_today_and_is_idempotent(pg):
    from app import services

    conn = pg.get_connection()
    try:
        conn.execute("INSERT INTO llm_calls (endpoint, cost_usd) VALUES ('/old', 2.5), ('/null', NULL)")
        conn.execute("INSERT INTO llm_calls (endpoint, cost_usd, created_at) "
                     "VALUES ('/yesterday', 99, '2000-01-01 00:00:00+00')")
        conn.execute("DELETE FROM ai_spend_daily")
        conn.commit()
        migration = (pg.PG_SCHEMA_PATH.parent / "pg" / "028_ai_spend_daily.sql").read_text(encoding="utf-8")
        conn._raw.execute(migration)
        conn.commit()
        assert services._spent_today(conn) == pytest.approx(2.5)
        conn.execute("DELETE FROM llm_calls")
        conn.commit()
        conn._raw.execute(migration)
        conn.commit()
        assert services._spent_today(conn) == pytest.approx(2.5)
    finally:
        conn.close()
