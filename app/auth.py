"""Authentication & role resolution (v1.2).

Validates Supabase-issued JWTs and maps each user to an application role
(analyst | executive | admin) stored in the app_roles table.

Two validation paths, chosen automatically:
  - HS256 with the shared SUPABASE_JWT_SECRET (Supabase's classic signing)
  - JWKS/asymmetric (RS256/ES256) fetched from the project's well-known JWKS
    endpoint, used when no shared secret is configured.

When auth is not active (no SUPABASE_URL — tests, local dev), every request is
treated as an anonymous admin so the zero-config experience is preserved.
"""

import time
from typing import Optional

import jwt
from fastapi import HTTPException, Request
from pydantic import BaseModel

from app.config import auth_active, settings
from app.db import get_connection

VALID_ROLES = ("viewer", "analyst", "executive", "admin")


class CurrentUser(BaseModel):
    id: str
    email: str
    role: str  # viewer | analyst | executive | admin


# Fixed sentinel UUIDs (not "local-dev"/"demo"): the Postgres schema types the
# attribution columns (user_id, uploaded_by, …) as UUID, so a non-UUID id would
# fail to insert on an auth-off / demo deployment backed by Postgres. Real users
# always carry Supabase UUIDs; these are the zero-config bypass identities.
ANONYMOUS_ADMIN = CurrentUser(id="00000000-0000-0000-0000-000000000000",
                              email="local@dev", role="admin")
# Demo sessions: a real, recognized identity with the most-restricted role, so
# every write is rejected by the normal role guards (403), not the auth gate.
DEMO_USER = CurrentUser(id="00000000-0000-0000-0000-0000000000de",
                        email="demo@closebrief.app", role="viewer")

# ---- JWKS cache (asymmetric keys), refreshed hourly ----
_jwks_client: Optional["jwt.PyJWKClient"] = None
_jwks_fetched_at = 0.0
_JWKS_TTL = 3600.0

# ---- role cache: user_id -> (role, expiry) ----
_role_cache: dict[str, tuple[str, float]] = {}
_ROLE_TTL = 300.0


def _jwks() -> "jwt.PyJWKClient":
    global _jwks_client, _jwks_fetched_at
    now = time.time()
    if _jwks_client is None or now - _jwks_fetched_at > _JWKS_TTL:
        url = settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(url)
        _jwks_fetched_at = now
    return _jwks_client


def _decode_token(token: str) -> dict:
    """Validate signature + expiry, return the claims. Raises on any failure.

    The algorithm is chosen from the token's own header: Supabase projects sign
    with HS256 (legacy shared secret) OR ES256/RS256 (asymmetric, validated via
    JWKS). We support both so it works regardless of the project's key setting."""
    alg = jwt.get_unverified_header(token).get("alg", "")

    if alg == "HS256" and settings.supabase_jwt_secret:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"verify_aud": True},
        )

    # Asymmetric: resolve the signing key from JWKS by the token's kid.
    signing_key = _jwks().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience="authenticated",
        options={"verify_aud": True},
    )


def _resolve_role(user_id: str, email: str) -> str:
    """Look up the app role, creating the row on first login. The very first
    user to sign in becomes 'admin'; everyone after defaults to 'analyst'.
    Cached for 5 minutes to avoid a DB hit on every request."""
    cached = _role_cache.get(user_id)
    if cached and cached[1] > time.time():
        return cached[0]

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT role FROM app_roles WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is not None:
            role = row["role"]
        else:
            count = conn.execute("SELECT COUNT(*) AS n FROM app_roles").fetchone()["n"]
            role = "admin" if count == 0 else "analyst"
            conn.execute(
                "INSERT INTO app_roles (user_id, email, role) VALUES (?, ?, ?)",
                (user_id, email, role),
            )
            conn.commit()
    finally:
        conn.close()

    _role_cache[user_id] = (role, time.time() + _ROLE_TTL)
    return role


def invalidate_role_cache(user_id: str | None = None) -> None:
    if user_id is None:
        _role_cache.clear()
    else:
        _role_cache.pop(user_id, None)


def authenticate(request: Request) -> CurrentUser:
    """Validate the request's bearer token and build the CurrentUser. Bypassed
    (anonymous admin) when auth is not active. Called by the middleware."""
    if not auth_active():
        return ANONYMOUS_ADMIN

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        # Demo mode: an anonymous visitor who opted into the demo gets the
        # read-only viewer identity (scoped to the demo dataset by the caller).
        if settings.demo_mode and request.headers.get("x-closebrief-demo") == "1":
            return DEMO_USER
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = header[7:].strip()

    try:
        claims = _decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception as exc:  # signature/format/JWKS failures
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

    user_id = claims.get("sub")
    email = claims.get("email") or claims.get("user_metadata", {}).get("email", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject")

    role = _resolve_role(user_id, email)
    return CurrentUser(id=user_id, email=email, role=role)


def get_current_user(request: Request) -> CurrentUser:
    """FastAPI dependency — returns the user the middleware attached. Falls back
    to authenticating inline if the middleware didn't run (e.g. in unit tests
    that call an endpoint function directly)."""
    user = getattr(request.state, "user", None)
    if user is None:
        user = authenticate(request)
    return user


def require_role(*roles: str):
    """Dependency factory — 403 if the current user's role isn't allowed."""

    def _dep(request: Request) -> CurrentUser:
        user = get_current_user(request)
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Requires role {' or '.join(roles)}; you are '{user.role}'",
            )
        return user

    return _dep
