"""Auth/RBAC enforcement that runs on BOTH backends.

test_auth.py is marked sqlite_only because it uses short string user ids that
Postgres's UUID attribution columns reject. This file uses real UUID subjects and
asserts only on status codes / roles (never on returned ids), so it also runs on
the Postgres CI job — covering the 401 / 403 / 200 enforcement paths on the prod
database, which would otherwise have zero Postgres coverage.
"""

import time
import uuid

import jwt
import pytest
from dbharness import use_test_db

SECRET = "test-jwt-secret-at-least-32-bytes-long-xyz"


def _uuid() -> str:
    return str(uuid.uuid4())


def _bearer(sub: str, email: str) -> dict:
    token = jwt.encode(
        {"sub": sub, "email": email, "aud": "authenticated", "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def _build(seed_roles: dict | None = None):
        import app.main as main
        from app.config import settings

        use_test_db(monkeypatch)
        monkeypatch.setattr(settings, "redis_url", "")
        monkeypatch.setattr(settings, "db_path", str(tmp_path / f"authpg_{time.time_ns()}.db"))
        monkeypatch.setattr(settings, "vector_backend", "faiss")
        monkeypatch.setattr(settings, "embedding_provider", "offline")
        monkeypatch.setattr(settings, "supabase_url", "https://test.supabase.co")
        monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
        monkeypatch.setattr(settings, "auth_enabled", True)

        from app.deps import shared_cache, shared_embedder, shared_vector_store
        shared_cache.cache_clear()
        shared_embedder.cache_clear()
        shared_vector_store.cache_clear()
        import app.auth as auth
        auth.invalidate_role_cache()

        main.init_db()
        if seed_roles:
            from app.db import get_connection
            conn = get_connection()
            try:
                for uid, (email, role) in seed_roles.items():
                    conn.execute(
                        "INSERT INTO app_roles (user_id, email, role) VALUES (?, ?, ?)",
                        (uid, email, role))
                conn.commit()
            finally:
                conn.close()

        from fastapi.testclient import TestClient
        return TestClient(main.app)

    return _build


def test_unauthenticated_rejected(make_client):
    c = make_client()
    assert c.get("/facts?period=2025-01").status_code == 401


def test_role_gating_read_vs_write(make_client):
    analyst, execu = _uuid(), _uuid()
    c = make_client({analyst: ("a@co.com", "analyst"), execu: ("e@co.com", "executive")})
    # require_read: analyst may read (empty board is 200, not 403).
    assert c.get("/facts?period=2025-01", headers=_bearer(analyst, "a@co.com")).status_code == 200
    # require_write excludes executive -> creating a derived KPI is 403 (before any body work).
    r = c.post("/kpis/derived", headers=_bearer(execu, "e@co.com"),
               json={"name": "X", "formula": "[A]"})
    assert r.status_code == 403


def test_first_user_becomes_admin(make_client):
    c = make_client()               # no seeded roles
    me = c.get("/me", headers=_bearer(_uuid(), "first@co.com")).json()
    assert me["role"] == "admin"
