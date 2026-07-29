"""Google Sign-In (v5.6, optional).

The browser runs Google Identity Services, which hands back a signed **ID token**.
That token is attacker-supplied input: anyone can POST arbitrary JSON here. So
every claim is verified against Google's published keys before it is trusted:

    signature (JWKS) · aud == our client id · iss · exp · alg pinned to RS256
    · email_verified

On success the token is mapped to a local app_roles row (linking an existing
account by verified email, never overwriting it) and we issue **our own** session
token. The frontend only ever stores ours — never Google's.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import jwt
from jwt import PyJWKClient

from app.auth import VALID_ROLES, CurrentUser, invalidate_role_cache
from app.config import google_enabled, session_secret, settings
from app.db import get_connection

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
# Google mints both forms; accept either.
_VALID_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
_JWKS_TTL = 3600.0            # ~1h: survives key rotation without per-request fetches
SESSION_TTL_SECONDS = 12 * 3600
# Marks a session token as ours (vs a Supabase one) so authenticate() routes it.
SESSION_ISSUER = "closebrief"


class GoogleAuthError(Exception):
    """Any failure to verify a credential. The endpoint turns this into a 401
    with this message, so the text must stay user-readable ("not configured" vs
    "email not verified" is the difference between retrying and giving up)."""


# ---------------------------------------------------------------- JWKS cache
_jwks_client: PyJWKClient | None = None
_jwks_fetched_at = 0.0
_jwks_lock = threading.Lock()   # the server is threaded (uvicorn workers + our pool)


def _new_jwks_client() -> PyJWKClient:
    return PyJWKClient(GOOGLE_CERTS_URL)


def _get_jwks(force_refresh: bool = False) -> PyJWKClient:
    """Google's key set, cached ~1h.

    force_refresh is used for exactly one case — an unknown `kid`, i.e. a key
    rotation. Refreshing on any other failure would let a flood of junk
    credentials become a flood of outbound requests to Google.

    If a refresh fails we keep serving the stale client rather than locking
    everyone out over a transient blip; we only raise if we never had one.
    """
    global _jwks_client, _jwks_fetched_at
    with _jwks_lock:
        fresh = _jwks_client is not None and (time.time() - _jwks_fetched_at) < _JWKS_TTL
        if fresh and not force_refresh:
            return _jwks_client
        try:
            client = _new_jwks_client()
            _jwks_client = client
            _jwks_fetched_at = time.time()
            return client
        except Exception as exc:  # noqa: BLE001 - transient network/Google blip
            if _jwks_client is not None:
                return _jwks_client   # stale beats an outage
            raise GoogleAuthError(f"Could not fetch Google's signing keys: {exc}") from exc


def _reset_jwks_cache() -> None:
    """Test hook — drop the cached key set."""
    global _jwks_client, _jwks_fetched_at
    with _jwks_lock:
        _jwks_client = None
        _jwks_fetched_at = 0.0


def _signing_key(token: str):
    """Resolve the signing key for this token, refetching once (and only once)
    if its `kid` is unknown — the one failure a refetch can actually fix."""
    try:
        return _get_jwks().get_signing_key_from_jwt(token).key
    except GoogleAuthError:
        raise
    except Exception:  # noqa: BLE001 - unknown kid, or a malformed/empty key set
        try:
            return _get_jwks(force_refresh=True).get_signing_key_from_jwt(token).key
        except GoogleAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - still unknown -> not a rotation
            raise GoogleAuthError(f"Unrecognised signing key: {exc}") from exc


# ------------------------------------------------------------- verification
def verify_google_token(credential: str) -> dict[str, Any]:
    """Verify a Google ID token and return its claims. Raises GoogleAuthError on
    every failure — never leaks a stack trace, never returns unverified data."""
    if not google_enabled():
        raise GoogleAuthError("Google sign-in is not configured on this server")
    if not credential or not isinstance(credential, str):
        raise GoogleAuthError("Missing Google credential")

    # Pin the algorithm from the *unverified* header first: this rejects
    # `alg: none` and HMAC-confusion (signing with the public key as an HMAC
    # secret) before any key material is involved.
    try:
        alg = jwt.get_unverified_header(credential).get("alg", "")
    except Exception as exc:  # noqa: BLE001 - not even a JWT
        raise GoogleAuthError(f"Malformed credential: {exc}") from exc
    if alg != "RS256":
        raise GoogleAuthError(f"Unsupported token algorithm '{alg}' (expected RS256)")

    key = _signing_key(credential)
    try:
        claims = jwt.decode(
            credential,
            key,
            algorithms=["RS256"],                    # pinned again at decode
            audience=settings.google_client_id,      # token minted for THIS app
            issuer=_VALID_ISSUERS,
            options={"verify_signature": True, "verify_aud": True,
                     "verify_exp": True, "verify_iss": True,
                     "require": ["aud", "iss", "exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise GoogleAuthError("Google credential has expired — try signing in again") from exc
    except jwt.InvalidAudienceError as exc:
        raise GoogleAuthError("Credential was issued for a different application") from exc
    except jwt.InvalidIssuerError as exc:
        raise GoogleAuthError("Credential was not issued by Google") from exc
    # Broad by design: some key/JWT libraries raise non-JWT errors (e.g. a
    # KeyError subclass) for a malformed key set, which would otherwise surface
    # as a 500 with a stack trace instead of a clean 401.
    except Exception as exc:  # noqa: BLE001
        raise GoogleAuthError(f"Invalid Google credential: {exc}") from exc

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleAuthError("Google credential has no email address")
    # Google will mint a token for an address the user has not proven they own;
    # trusting it would let someone sign in as that address's real owner.
    if claims.get("email_verified") is not True:
        raise GoogleAuthError("Your Google email address is not verified")
    claims["email"] = email
    return claims


# ---------------------------------------------------------- account mapping
def _admin_allowlist() -> set[str]:
    return {e.strip().lower() for e in (settings.google_admin_emails or "").split(",") if e.strip()}


def _role_rank(role: str) -> int:
    """Position in VALID_ROLES = privilege order (viewer < analyst < executive
    < admin), so we can compare without hardcoding a second list."""
    return VALID_ROLES.index(role) if role in VALID_ROLES else 0


def find_or_create_user(email: str, google_sub: str) -> CurrentUser:
    """Map a verified Google identity to a local app_roles row.

    - An existing account with this address is LINKED, not duplicated: the email
      came from a verified Google claim, so it is the same person. We keep their
      existing row (and, on a Supabase deploy, their password login) untouched.
    - Roles are only ever RAISED. An admin who signs in with Google while not on
      the allowlist must not be demoted to the default role.
    """
    email = (email or "").strip().lower()
    default_role = settings.google_default_role if settings.google_default_role in VALID_ROLES else "viewer"
    target_role = "admin" if email in _admin_allowlist() else default_role

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, role FROM app_roles WHERE lower(email) = ?", (email,)
        ).fetchone()
        if row is not None:
            user_id, current_role = str(row["user_id"]), row["role"]
            # Raise-only: never lower an existing role.
            if _role_rank(target_role) > _role_rank(current_role):
                conn.execute(
                    "UPDATE app_roles SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (target_role, user_id),
                )
                conn.commit()
                invalidate_role_cache(user_id)
                current_role = target_role
            return CurrentUser(id=user_id, email=email, role=current_role)

        # New user. The id is Google's stable subject, namespaced into a UUID so
        # it fits the Postgres UUID attribution columns.
        user_id = _uuid_for_google_sub(google_sub)
        conn.execute(
            "INSERT INTO app_roles (user_id, email, role) VALUES (?, ?, ?)",
            (user_id, email, target_role),
        )
        conn.commit()
        return CurrentUser(id=user_id, email=email, role=target_role)
    finally:
        conn.close()


def _uuid_for_google_sub(google_sub: str) -> str:
    """Deterministic UUIDv5 from Google's subject id: stable across logins and
    valid for the Postgres UUID columns (which reject raw numeric Google subs)."""
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://accounts.google.com/{google_sub}"))


# --------------------------------------------------------- session issuance
def issue_session_token(user: CurrentUser) -> str:
    """Mint Closebrief's own session token. The frontend stores THIS, never
    Google's credential. Verified by app.auth on subsequent requests."""
    secret = session_secret()
    if not secret:
        raise GoogleAuthError("Server is missing a session signing secret")
    now = int(time.time())
    return jwt.encode(
        {"sub": user.id, "email": user.email, "role": user.role,
         "iss": SESSION_ISSUER, "aud": "authenticated",
         "iat": now, "exp": now + SESSION_TTL_SECONDS},
        secret, algorithm="HS256",
    )


def sign_in_with_google(credential: str) -> dict[str, str]:
    """Full flow: verify -> map to a local user -> issue our session token."""
    claims = verify_google_token(credential)
    user = find_or_create_user(claims["email"], str(claims.get("sub") or ""))
    return {"token": issue_session_token(user), "role": user.role, "email": user.email}
