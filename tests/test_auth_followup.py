"""Auth boundary regressions, using real signed tokens and isolated SQLite."""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.requests import Request


@pytest.mark.parametrize("provider", ["session", "supabase", "asymmetric"])
def test_auth_rejects_signed_tokens_without_expiration(monkeypatch, provider):
    from app import auth
    from app.config import settings
    secret = "offline-auth-followup-test-secret-32-bytes"
    monkeypatch.setattr(settings, "supabase_url", "https://example.invalid")
    monkeypatch.setattr(settings, "supabase_jwt_secret", secret)
    monkeypatch.setattr(settings, "session_jwt_secret", secret)
    claims = {"sub": "user", "aud": "authenticated"}
    if provider == "session":
        claims["iss"] = "closebrief"
    key, alg = secret, "HS256"
    if provider == "asymmetric":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        alg = "RS256"
        monkeypatch.setattr(auth, "_jwks", lambda: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())))
    token = jwt.encode(claims, key, algorithm=alg)
    with pytest.raises(jwt.MissingRequiredClaimError):
        auth._decode_token(token)


@pytest.mark.parametrize("metadata", [None, [], "invalid"])
def test_auth_handles_non_object_optional_user_metadata(monkeypatch, metadata):
    from app import auth
    from app.config import settings
    secret = "offline-auth-followup-test-secret-32-bytes"
    monkeypatch.setattr(settings, "supabase_url", "https://example.invalid")
    monkeypatch.setattr(settings, "supabase_jwt_secret", secret)
    monkeypatch.setattr(settings, "session_jwt_secret", secret)
    monkeypatch.setattr(auth, "_resolve_role", lambda uid, email: "analyst")
    token = jwt.encode({"sub": "user", "aud": "authenticated",
                        "exp": int(time.time()) + 60, "user_metadata": metadata},
                       secret, algorithm="HS256")
    request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
    user = auth.authenticate(request)
    assert user.id == "user"
    assert user.email == ""


def test_google_subject_survives_verified_email_change(tmp_path, monkeypatch):
    from app import db, google_auth
    from app.config import settings
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "auth.db"))
    db.init_db()
    original = google_auth.find_or_create_user("old@example.com", "stable-google-subject")
    renamed = google_auth.find_or_create_user("new@example.com", "stable-google-subject")
    assert renamed.id == original.id
    assert renamed.email == "new@example.com"
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT email FROM app_roles").fetchall()
        assert [r["email"] for r in rows] == ["new@example.com"]
    finally:
        conn.close()


@pytest.mark.parametrize("other_scope", ["workspace", "demo"])
def test_delete_dataset_cannot_delete_outside_current_scope(tmp_path, monkeypatch, other_scope):
    from app import datasets, db
    from app.config import settings
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "datasets.db"))
    db.init_db()
    conn = db.get_connection()
    try:
        datasets.set_demo_scope(False)
        datasets.set_workspace_scope(1)
        owned = datasets.create_dataset(conn, "Private", activate=False)
        if other_scope == "workspace":
            datasets.set_workspace_scope(2)
        else:
            datasets.set_demo_scope(True)
        assert datasets.delete_dataset(conn, owned) is False
        assert conn.execute("SELECT name FROM datasets WHERE id = ?", (owned,)).fetchone()["name"] == "Private"
    finally:
        datasets.set_demo_scope(False)
        datasets.set_workspace_scope(None)
        conn.close()
