"""HTTP routes moved from app.main: workspaces."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app import audit, workspaces
from app.auth import CurrentUser, get_current_user
from app.config import auth_active, google_enabled, settings
from app.db import get_connection
from app.google_auth import GoogleAuthError, sign_in_with_google

router = APIRouter()


@router.get("/auth/config")
def auth_config() -> dict:
    """Public: the frontend uses this to decide whether to show the login gate
    and to initialize the Supabase JS client. Never returns secrets."""
    return {
        "auth_enabled": auth_active(),
        "supabase_url": settings.supabase_url,
        "supabase_anon_key": settings.supabase_anon_key,
        # Public by design (a DSN is not a secret); enables frontend error tracking.
        "sentry_dsn": settings.sentry_dsn,
        "environment": settings.environment,
        "demo_enabled": settings.demo_mode,
        # Google sign-in (optional). The client id is designed to be embedded in
        # a web page, so exposing it is safe. Empty + google_enabled false means
        # the frontend never loads Google's script at all.
        "google_client_id": settings.google_client_id if google_enabled() else "",
        "google_enabled": google_enabled(),
        "allow_guest": settings.allow_guest,
        "auth_required": auth_active() and not settings.allow_guest,
    }


@router.post("/auth/google")
def auth_google(payload: dict) -> dict:
    """Exchange a Google ID token for a Closebrief session token.

    Open (the caller has no token yet) and rate-limited by the telemetry
    middleware. The credential is attacker-supplied: every failure path returns
    401 with a readable reason, never a stack trace."""
    if not google_enabled():
        raise HTTPException(status_code=401,
                            detail="Google sign-in is not configured on this server")
    credential = (payload or {}).get("credential")
    try:
        result = sign_in_with_google(credential)
    except GoogleAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    conn = get_connection()
    try:
        audit.record(conn, "login", "user", result["email"], actor_email=result["email"],
                     summary={"provider": "google", "role": result["role"]})
    except Exception:  # noqa: BLE001 - audit must never block a successful login
        pass
    finally:
        conn.close()
    return result


@router.get("/me")
def whoami(request: Request, user: CurrentUser = Depends(get_current_user)) -> dict:
    """The current user's identity, global role, and (if in a workspace) their
    per-workspace role."""
    return {"id": user.id, "email": user.email, "role": user.role,
            "workspace_role": getattr(request.state, "ws_role", None)}


@router.get("/workspaces")
def list_workspaces(request: Request, user: CurrentUser = Depends(get_current_user)) -> dict:
    """The caller's workspaces + which one is active for this request."""
    conn = get_connection()
    try:
        mine = workspaces.list_user_workspaces(conn, user.id)
        active = getattr(request.state, "workspace_id", None)
        return {"active_id": active, "workspaces": mine}
    finally:
        conn.close()


@router.get("/workspaces/{ws_id}/members")
def list_ws_members(ws_id: int, user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    conn = get_connection()
    try:
        if auth_active() and not workspaces.is_member(conn, ws_id, user.id):
            raise HTTPException(status_code=404, detail="Workspace not found")
        return workspaces.list_members(conn, ws_id)
    finally:
        conn.close()
