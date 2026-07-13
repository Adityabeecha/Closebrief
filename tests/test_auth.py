"""Auth & RBAC tests (v1.2). Activates auth with a test HS256 secret and mints
real Supabase-shaped tokens, so the full validation path is exercised without a
live Supabase project."""

import time

import jwt
import pytest

SECRET = "test-jwt-secret-at-least-32-bytes-long-xyz"


def _token(sub="u1", email="u1@co.com", exp_delta=3600, aud="authenticated"):
    return jwt.encode(
        {"sub": sub, "email": email, "aud": aud, "exp": int(time.time()) + exp_delta},
        SECRET, algorithm="HS256",
    )


def _bearer(**kw):
    return {"Authorization": f"Bearer {_token(**kw)}"}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Factory: build a TestClient with auth active or bypassed. Each call uses
    an isolated SQLite DB and clears the module-level auth/role caches."""
    import app.main as main
    from app.config import settings

    def _build(auth_on: bool, seed_roles: dict | None = None):
        monkeypatch.setattr(settings, "database_url", "")
        monkeypatch.setattr(settings, "redis_url", "")
        monkeypatch.setattr(settings, "db_path", str(tmp_path / f"auth_{auth_on}_{time.time_ns()}.db"))
        monkeypatch.setattr(settings, "vector_backend", "faiss")
        monkeypatch.setattr(settings, "embedding_provider", "offline")
        monkeypatch.setattr(settings, "supabase_url", "https://test.supabase.co" if auth_on else "")
        monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET if auth_on else "")
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
                        (uid, email, role),
                    )
                conn.commit()
            finally:
                conn.close()

        from fastapi.testclient import TestClient
        return TestClient(main.app)

    return _build


# ---------- authentication (401) ----------

def test_unauthenticated_request_rejected(make_client):
    c = make_client(auth_on=True)
    assert c.get("/facts?period=2025-01").status_code == 401


def test_per_workspace_admin_enforced(make_client):
    """v4.0 follow-up: workspace-scoped admin actions are gated by the caller's
    MEMBERSHIP role in the active workspace, not their global role."""
    c = make_client(auth_on=True)
    from app.db import get_connection
    from app.workspaces import add_member, create_workspace
    conn = get_connection()
    try:
        w = create_workspace(conn, "W", "a1", "a1@co.com")   # a1 = admin
        add_member(conn, w, "a2", "a2@co.com", "analyst")     # a2 = analyst
    finally:
        conn.close()
    hdr = {"X-Workspace-Id": str(w)}
    # /me surfaces the per-workspace role.
    assert c.get("/me", headers={**_bearer(sub="a1", email="a1@co.com"), **hdr}).json()["workspace_role"] == "admin"
    assert c.get("/me", headers={**_bearer(sub="a2", email="a2@co.com"), **hdr}).json()["workspace_role"] == "analyst"
    # Creating an invite is a workspace-admin action: admin ok, analyst 403.
    a1 = c.post(f"/workspaces/{w}/invites", json={"role": "analyst"},
                headers={**_bearer(sub="a1", email="a1@co.com"), **hdr})
    a2 = c.post(f"/workspaces/{w}/invites", json={"role": "analyst"},
                headers={**_bearer(sub="a2", email="a2@co.com"), **hdr})
    assert a1.status_code == 201 and a2.status_code == 403


def test_invalid_jwt_rejected(make_client):
    c = make_client(auth_on=True)
    assert c.get("/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_expired_jwt_rejected(make_client):
    c = make_client(auth_on=True)
    r = c.get("/me", headers=_bearer(exp_delta=-10))
    assert r.status_code == 401


def test_valid_jwt_accepted(make_client):
    c = make_client(auth_on=True)
    r = c.get("/me", headers=_bearer(sub="u1", email="u1@co.com"))
    assert r.status_code == 200
    assert r.json()["email"] == "u1@co.com"


def test_wrong_audience_rejected(make_client):
    c = make_client(auth_on=True)
    r = c.get("/me", headers=_bearer(aud="wrong"))
    assert r.status_code == 401


# ---------- roles (403) ----------

def test_role_analyst_can_ingest(make_client):
    c = make_client(auth_on=True, seed_roles={"a1": ("a@co.com", "analyst")})
    csv = "period,metric,value,budget\n2025-01,Rev,100,90\n"
    r = c.post("/ingest", files={"file": ("f.csv", csv, "text/csv")}, headers=_bearer(sub="a1", email="a@co.com"))
    assert r.status_code == 200, r.text


def test_role_executive_cannot_ingest(make_client):
    c = make_client(auth_on=True, seed_roles={"e1": ("e@co.com", "executive")})
    csv = "period,metric,value,budget\n2025-01,Rev,100,90\n"
    r = c.post("/ingest", files={"file": ("f.csv", csv, "text/csv")}, headers=_bearer(sub="e1", email="e@co.com"))
    assert r.status_code == 403


def test_role_executive_can_read_facts(make_client):
    c = make_client(auth_on=True, seed_roles={"e1": ("e@co.com", "executive")})
    r = c.get("/facts?period=2025-01", headers=_bearer(sub="e1", email="e@co.com"))
    assert r.status_code == 200


def test_role_admin_can_manage_users(make_client):
    c = make_client(auth_on=True, seed_roles={"ad": ("ad@co.com", "admin")})
    r = c.get("/admin/users", headers=_bearer(sub="ad", email="ad@co.com"))
    assert r.status_code == 200


def test_role_analyst_cannot_manage_users(make_client):
    c = make_client(auth_on=True, seed_roles={"a1": ("a@co.com", "analyst")})
    r = c.get("/admin/users", headers=_bearer(sub="a1", email="a@co.com"))
    assert r.status_code == 403


def test_first_user_becomes_admin(make_client):
    c = make_client(auth_on=True)  # empty app_roles
    r = c.get("/me", headers=_bearer(sub="first", email="first@co.com"))
    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    # second user defaults to analyst
    import app.auth as auth
    auth.invalidate_role_cache()
    r2 = c.get("/me", headers=_bearer(sub="second", email="second@co.com"))
    assert r2.json()["role"] == "analyst"


# ---------- bypass ----------

def test_auth_bypass_when_disabled(make_client):
    c = make_client(auth_on=False)
    # No token, yet reads and admin endpoints are open (anonymous admin).
    assert c.get("/facts?period=2025-01").status_code == 200
    assert c.get("/admin/users").status_code == 200


# ---------- attribution ----------

def test_user_attribution_on_feedback(make_client):
    c = make_client(auth_on=True, seed_roles={"a1": ("a@co.com", "analyst")})
    hdr = _bearer(sub="a1", email="a@co.com")
    # Seed a report row directly, then post feedback and check attribution.
    from app.db import get_connection
    from app.workspaces import ensure_user_workspace
    conn = get_connection()
    try:
        # v4.0: the report's dataset must belong to the caller's workspace, else
        # tenant scoping (correctly) hides it. a1 resolves to this workspace.
        ws = ensure_user_workspace(conn, "a1", "a@co.com")
        conn.execute("INSERT INTO datasets (name, is_active, workspace_id) VALUES ('d', 1, ?)", (ws,))
        conn.execute("INSERT INTO metrics (dataset_id, name) VALUES (1, 'Rev')")
        conn.execute(
            "INSERT INTO generated_reports (metric_id, period, confidence, faithfulness) VALUES (1,'2025-01','High','passed')"
        )
        conn.commit()
        rid = conn.execute("SELECT id FROM generated_reports ORDER BY id DESC LIMIT 1").fetchone()["id"]
    finally:
        conn.close()

    r = c.post("/feedback", json={"report_id": rid, "action": "accepted"}, headers=hdr)
    assert r.status_code == 201

    conn = get_connection()
    try:
        row = conn.execute("SELECT user_id, user_email FROM feedback WHERE report_id = ?", (rid,)).fetchone()
    finally:
        conn.close()
    assert row["user_id"] == "a1" and row["user_email"] == "a@co.com"


def test_user_attribution_on_report(make_client):
    make_client(auth_on=True, seed_roles={"a1": ("a@co.com", "analyst")})
    from app.auth import CurrentUser

    # Seed metric in active dataset so _persist_report resolves it.
    from app.db import get_connection
    from app.main import _persist_report
    from app.schemas import Deltas, InsightOutput
    conn = get_connection()
    try:
        conn.execute("INSERT INTO datasets (name, is_active) VALUES ('d', 1)")
        conn.execute("INSERT INTO metrics (dataset_id, name) VALUES (1, 'Rev')")
        conn.commit()
    finally:
        conn.close()

    insight = InsightOutput(metric="Rev", category="X", period="2025-01", value=1.0,
                            unit="USD", deltas=Deltas(), is_anomaly=False,
                            confidence="High", faithfulness="passed")
    user = CurrentUser(id="a1", email="a@co.com", role="analyst")
    rid = _persist_report(insight, user)

    conn = get_connection()
    try:
        row = conn.execute("SELECT user_id, user_email FROM generated_reports WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()
    assert row["user_id"] == "a1" and row["user_email"] == "a@co.com"
