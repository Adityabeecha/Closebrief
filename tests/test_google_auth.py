"""Google Sign-In (v5.6): ID-token verification, account mapping, and guest mode.

A Google credential is attacker-supplied input, so these tests are adversarial:
they assert that a *correctly shaped but wrongly signed* token is rejected, that
the algorithm is pinned, and that junk traffic can't turn into a flood of
outbound requests to Google.

No network: a local RSA key pair stands in for Google's, injected via a fake
JWKS client.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from dbharness import use_test_db

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
SESSION_SECRET = "session-signing-secret-at-least-32-bytes-long"
KID = "test-key-1"


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def keypair():
    """One RSA key pair for the module: 'Google's' signing key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture(scope="module")
def rogue_key():
    """A different, valid RSA key — the 'self-signed token' attacker."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _id_token(private_key, *, aud=CLIENT_ID, iss="https://accounts.google.com",
              email="user@example.com", email_verified=True, sub="1234567890",
              exp_delta=3600, alg="RS256", kid=KID):
    claims = {
        "aud": aud, "iss": iss, "sub": sub, "email": email,
        "email_verified": email_verified,
        "iat": int(time.time()), "exp": int(time.time()) + exp_delta,
    }
    return jwt.encode(claims, private_key, algorithm=alg, headers={"kid": kid})


class _FakeJWKSClient:
    """Stands in for jwt.PyJWKClient. Counts constructions so we can assert how
    many times the key set was (re)fetched."""
    instances = 0
    known_kid = KID

    def __init__(self, public_key):
        self._public_key = public_key
        type(self).instances += 1

    def get_signing_key_from_jwt(self, token):
        kid = jwt.get_unverified_header(token).get("kid")
        if kid != type(self).known_kid:
            raise jwt.PyJWKClientError(f"Unable to find a signing key that matches: {kid}")
        return type("_Key", (), {"key": self._public_key})()


@pytest.fixture
def google_on(monkeypatch, keypair):
    """Google sign-in configured, with Google's JWKS replaced by our local key."""
    from app import google_auth
    from app.config import settings

    _, public_key = keypair
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "session_jwt_secret", SESSION_SECRET)
    monkeypatch.setattr(settings, "google_default_role", "viewer")
    monkeypatch.setattr(settings, "google_admin_emails", "")
    _FakeJWKSClient.instances = 0
    _FakeJWKSClient.known_kid = KID
    monkeypatch.setattr(google_auth, "_new_jwks_client", lambda: _FakeJWKSClient(public_key))
    google_auth._reset_jwks_cache()
    yield
    google_auth._reset_jwks_cache()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A database for the account-mapping tests."""
    from app.config import settings
    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "g.db"))
    from app.db import init_db
    init_db()
    from app.auth import invalidate_role_cache
    invalidate_role_cache()


# -------------------------------------------------------------- verification
def test_rejects_garbage_string(google_on):
    from app.google_auth import GoogleAuthError, verify_google_token
    for junk in ["", "not-a-token", "a.b.c", "...", "eyJhbGciOiJSUzI1NiJ9.garbage"]:
        with pytest.raises(GoogleAuthError):
            verify_google_token(junk)


def test_rejects_self_signed_token(google_on, rogue_key):
    """The load-bearing one: a token with a perfect shape and all the right
    claims, signed by someone who is not Google, must fail. If signature
    verification were skipped, this would pass and anyone could be anyone."""
    from app.google_auth import GoogleAuthError, verify_google_token
    forged = _id_token(rogue_key, email="attacker@example.com")
    with pytest.raises(GoogleAuthError):
        verify_google_token(forged)


def test_rejects_non_rs256_algorithm(google_on):
    """alg:none / HMAC-confusion are rejected from the unverified header, before
    any key material is used."""
    from app.google_auth import GoogleAuthError, verify_google_token
    hs = jwt.encode({"aud": CLIENT_ID, "iss": "https://accounts.google.com",
                     "sub": "1", "email": "a@b.com", "email_verified": True,
                     "exp": int(time.time()) + 60},
                    "an-attacker-supplied-hmac-secret-32b", algorithm="HS256",
                    headers={"kid": KID})
    with pytest.raises(GoogleAuthError, match="RS256"):
        verify_google_token(hs)


def test_rejects_wrong_audience(google_on, keypair):
    """A token minted for a DIFFERENT Google app is still signed by Google."""
    private, _ = keypair
    from app.google_auth import GoogleAuthError, verify_google_token
    with pytest.raises(GoogleAuthError):
        verify_google_token(_id_token(private, aud="someone-elses-app.apps.googleusercontent.com"))


def test_rejects_wrong_issuer(google_on, keypair):
    private, _ = keypair
    from app.google_auth import GoogleAuthError, verify_google_token
    with pytest.raises(GoogleAuthError):
        verify_google_token(_id_token(private, iss="https://evil.example.com"))


def test_rejects_expired_token(google_on, keypair):
    private, _ = keypair
    from app.google_auth import GoogleAuthError, verify_google_token
    with pytest.raises(GoogleAuthError):
        verify_google_token(_id_token(private, exp_delta=-60))


def test_rejects_unverified_email(google_on, keypair):
    """Google mints tokens for addresses the user hasn't proven they own."""
    private, _ = keypair
    from app.google_auth import GoogleAuthError, verify_google_token
    with pytest.raises(GoogleAuthError, match="not verified"):
        verify_google_token(_id_token(private, email_verified=False))


def test_accepts_valid_token_and_both_issuer_forms(google_on, keypair):
    private, _ = keypair
    from app.google_auth import verify_google_token
    for iss in ("accounts.google.com", "https://accounts.google.com"):
        claims = verify_google_token(_id_token(private, iss=iss))
        assert claims["email"] == "user@example.com" and claims["sub"] == "1234567890"


def test_refuses_cleanly_when_unconfigured(monkeypatch, keypair):
    """No client id -> a clean error, and zero contact with Google."""
    from app import google_auth
    from app.config import settings
    private, _ = keypair
    monkeypatch.setattr(settings, "google_client_id", "")
    monkeypatch.setattr(settings, "session_jwt_secret", SESSION_SECRET)
    calls = []
    monkeypatch.setattr(google_auth, "_new_jwks_client", lambda: calls.append(1))
    with pytest.raises(google_auth.GoogleAuthError, match="not configured"):
        google_auth.verify_google_token(_id_token(private))
    assert calls == [], "unconfigured server must not contact Google"


# --------------------------------------------------------------- JWKS cache
def test_unknown_kid_triggers_exactly_one_refetch(google_on, keypair):
    """A key id we don't recognise is the one failure a refetch can fix
    (rotation) — but it must be tried once, not in a loop."""
    private, _ = keypair
    from app.google_auth import GoogleAuthError, verify_google_token
    _FakeJWKSClient.instances = 0
    with pytest.raises(GoogleAuthError):
        verify_google_token(_id_token(private, kid="rotated-away-key"))
    # 1 initial fetch + exactly 1 refetch.
    assert _FakeJWKSClient.instances == 2


def test_junk_credentials_do_not_refetch_repeatedly(google_on, rogue_key):
    """A flood of bad signatures must NOT become a flood of requests to Google:
    only an unknown kid refetches, and a wrong signature is not that."""
    from app.google_auth import GoogleAuthError, verify_google_token
    verify_google_token  # populate the cache with one good fetch
    _FakeJWKSClient.instances = 0
    for _ in range(10):
        with pytest.raises(GoogleAuthError):
            verify_google_token(_id_token(rogue_key))   # known kid, bad signature
    assert _FakeJWKSClient.instances <= 1, "bad signatures must not refetch the key set"


def test_cached_key_set_is_reused_within_ttl(google_on, keypair):
    private, _ = keypair
    from app.google_auth import verify_google_token
    _FakeJWKSClient.instances = 0
    for _ in range(5):
        verify_google_token(_id_token(private))
    assert _FakeJWKSClient.instances == 1, "key set should be fetched once, then cached"


def test_stale_key_set_served_when_refresh_fails(google_on, keypair, monkeypatch):
    """A transient Google blip must not lock everyone out."""
    from app import google_auth
    private, _ = keypair
    google_auth.verify_google_token(_id_token(private))       # prime the cache
    monkeypatch.setattr(google_auth, "_jwks_fetched_at", 0.0)  # force it stale
    monkeypatch.setattr(google_auth, "_new_jwks_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("google down")))
    claims = google_auth.verify_google_token(_id_token(private))
    assert claims["email"] == "user@example.com"


# ---------------------------------------------------------- account mapping
def _conn():
    from app.db import get_connection
    return get_connection()


def test_new_google_user_created_with_configured_role(google_on, db, monkeypatch):
    from app.config import settings
    from app.google_auth import find_or_create_user
    monkeypatch.setattr(settings, "google_default_role", "viewer")
    user = find_or_create_user("new@example.com", "google-sub-1")
    assert user.role == "viewer" and user.email == "new@example.com"
    conn = _conn()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM app_roles").fetchone()["n"]
    finally:
        conn.close()
    assert n == 1


def test_signing_in_twice_reuses_one_row(google_on, db):
    from app.google_auth import find_or_create_user
    a = find_or_create_user("repeat@example.com", "google-sub-2")
    b = find_or_create_user("repeat@example.com", "google-sub-2")
    assert a.id == b.id
    conn = _conn()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM app_roles").fetchone()["n"]
    finally:
        conn.close()
    assert n == 1


def test_google_signin_never_demotes_an_admin(google_on, db, monkeypatch):
    """An admin signing in with Google while not on the allowlist keeps admin."""
    from app.config import settings
    from app.google_auth import find_or_create_user
    monkeypatch.setattr(settings, "google_default_role", "viewer")
    monkeypatch.setattr(settings, "google_admin_emails", "")
    conn = _conn()
    try:
        conn.execute("INSERT INTO app_roles (user_id, email, role) VALUES (?, ?, ?)",
                     ("11111111-1111-1111-1111-111111111111", "boss@example.com", "admin"))
        conn.commit()
    finally:
        conn.close()
    user = find_or_create_user("boss@example.com", "google-sub-3")
    assert user.role == "admin", "Google sign-in must never lower an existing role"


def test_google_signin_can_promote_allowlisted_email(google_on, db, monkeypatch):
    from app.config import settings
    from app.google_auth import find_or_create_user
    monkeypatch.setattr(settings, "google_admin_emails", "chief@example.com")
    conn = _conn()
    try:
        conn.execute("INSERT INTO app_roles (user_id, email, role) VALUES (?, ?, ?)",
                     ("22222222-2222-2222-2222-222222222222", "chief@example.com", "viewer"))
        conn.commit()
    finally:
        conn.close()
    assert find_or_create_user("chief@example.com", "google-sub-4").role == "admin"


def test_admin_allowlist_is_case_insensitive(google_on, db, monkeypatch):
    from app.config import settings
    from app.google_auth import find_or_create_user
    monkeypatch.setattr(settings, "google_admin_emails", " Chief@Example.COM , other@x.com ")
    assert find_or_create_user("CHIEF@example.com", "google-sub-5").role == "admin"


def test_linking_keeps_the_existing_account_row(google_on, db):
    """An existing account with the same verified address is LINKED, not
    duplicated or overwritten — the user keeps their id (and their password
    login, which lives in the identity provider, untouched)."""
    from app.google_auth import find_or_create_user
    existing_id = "33333333-3333-3333-3333-333333333333"
    conn = _conn()
    try:
        conn.execute("INSERT INTO app_roles (user_id, email, role) VALUES (?, ?, ?)",
                     (existing_id, "both@example.com", "analyst"))
        conn.commit()
    finally:
        conn.close()
    user = find_or_create_user("both@example.com", "google-sub-6")
    assert user.id == existing_id, "must link to the existing row, not create a new one"
    assert user.role == "analyst"
    conn = _conn()
    try:
        rows = conn.execute("SELECT user_id FROM app_roles WHERE lower(email) = ?",
                            ("both@example.com",)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "no duplicate account"


def test_email_matching_is_case_insensitive(google_on, db):
    from app.google_auth import find_or_create_user
    a = find_or_create_user("Mixed@Example.com", "google-sub-7")
    b = find_or_create_user("mixed@example.com", "google-sub-7")
    assert a.id == b.id


# ------------------------------------------------------------- session token
def test_session_token_round_trip(google_on, db):
    """The token we hand the frontend is ours, and app.auth accepts it."""
    from app.auth import _decode_token
    from app.google_auth import find_or_create_user, issue_session_token
    user = find_or_create_user("session@example.com", "google-sub-8")
    claims = _decode_token(issue_session_token(user))
    assert claims["sub"] == user.id and claims["iss"] == "closebrief"


def test_sign_in_with_google_end_to_end(google_on, db, keypair):
    private, _ = keypair
    from app.google_auth import sign_in_with_google
    out = sign_in_with_google(_id_token(private, email="e2e@example.com", sub="sub-e2e"))
    assert out["email"] == "e2e@example.com" and out["role"] == "viewer" and out["token"]


# ------------------------------------------------------------------ endpoint
@pytest.fixture
def client(tmp_path, monkeypatch, keypair):
    """App with Google sign-in configured (auth therefore active)."""
    import app.main as main
    from app import google_auth
    from app.config import settings

    _, public_key = keypair
    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "ep.db"))
    monkeypatch.setattr(settings, "vector_backend", "faiss")
    monkeypatch.setattr(settings, "embedding_provider", "offline")
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "session_jwt_secret", SESSION_SECRET)
    monkeypatch.setattr(settings, "google_default_role", "viewer")
    monkeypatch.setattr(settings, "google_admin_emails", "")
    monkeypatch.setattr(settings, "allow_guest", False)
    _FakeJWKSClient.instances = 0
    _FakeJWKSClient.known_kid = KID
    monkeypatch.setattr(google_auth, "_new_jwks_client", lambda: _FakeJWKSClient(public_key))
    google_auth._reset_jwks_cache()

    from app.auth import invalidate_role_cache
    from app.deps import shared_cache, shared_embedder, shared_vector_store
    shared_cache.cache_clear()
    shared_embedder.cache_clear()
    shared_vector_store.cache_clear()
    invalidate_role_cache()

    main.init_db()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c
    google_auth._reset_jwks_cache()


def test_auth_config_advertises_google(client):
    """The login screen reads this BEFORE any token exists, so it must be open."""
    cfg = client.get("/auth/config").json()
    assert cfg["google_enabled"] is True
    assert cfg["google_client_id"] == CLIENT_ID      # safe to expose
    assert cfg["allow_guest"] is False and cfg["auth_required"] is True


def test_auth_google_endpoint_issues_a_session(client, keypair):
    private, _ = keypair
    r = client.post("/auth/google", json={"credential": _id_token(private, email="ep@example.com")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "ep@example.com" and body["role"] == "viewer"
    # The issued token works on a normally-gated endpoint.
    me = client.get("/me", headers={"Authorization": "Bearer " + body["token"]})
    assert me.status_code == 200 and me.json()["email"] == "ep@example.com"


def test_auth_google_endpoint_401s_on_bad_credential(client, rogue_key):
    for payload in ({}, {"credential": ""}, {"credential": "garbage"},
                    {"credential": _id_token(rogue_key)}):
        r = client.post("/auth/google", json=payload)
        assert r.status_code == 401, payload
        assert r.json()["detail"]        # a readable reason, not a stack trace


def test_gated_endpoint_still_401s_without_a_token(client):
    assert client.get("/facts?period=2025-01").status_code == 401


# --------------------------------------------------------------- guest mode
@pytest.fixture
def guest_client(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "allow_guest", True)
    return client


def test_guest_can_read_when_enabled(guest_client):
    r = guest_client.get("/facts?period=2025-01")
    assert r.status_code == 200, "guests should get read access when ALLOW_GUEST is on"
    assert guest_client.get("/me").json()["role"] == "viewer"


def test_guest_cannot_perform_privileged_actions(guest_client):
    """Read-only: guest writes are refused by the normal role guards (403), and
    admin surfaces stay closed."""
    assert guest_client.get("/admin/users").status_code == 403
    r = guest_client.post("/reports/1/review", json={"status": "approved"})
    assert r.status_code == 403


def test_guest_off_means_401(client):
    assert client.get("/facts?period=2025-01").status_code == 401


def test_invalid_token_is_401_even_with_guests_on(guest_client):
    """A bad token is an error, not an anonymous visit — it must not silently
    downgrade to guest access."""
    r = guest_client.get("/facts?period=2025-01",
                         headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_auth_config_reports_guest_mode(guest_client):
    cfg = guest_client.get("/auth/config").json()
    assert cfg["allow_guest"] is True and cfg["auth_required"] is False
