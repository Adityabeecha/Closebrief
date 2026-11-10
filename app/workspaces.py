"""Workspace membership & invites (v4.0 multi-tenancy).

A workspace is a tenant; membership gates which workspace's data a user may
scope to. The active workspace is resolved server-side from verified membership
(never trusted from the client). Roles are per-workspace here.
"""

from __future__ import annotations

import secrets
from threading import RLock
from time import monotonic

WS_ROLES = ("admin", "analyst", "executive")
_MEMBERSHIP_TTL = 60.0
_MEMBERSHIP_CACHE_MAX = 1024
_membership_cache: dict[tuple[str, int | None], tuple[float, tuple[int, str]]] = {}
_membership_lock = RLock()


def invalidate_membership_cache(user_id: str | None = None) -> None:
    """Forget one user's workspace resolutions, or all resolutions."""
    with _membership_lock:
        if user_id is None:
            _membership_cache.clear()
        else:
            for key in [key for key in _membership_cache if key[0] == user_id]:
                del _membership_cache[key]



def create_workspace(conn, name: str, owner_id: str, owner_email: str | None = None) -> int:
    cur = conn.execute("INSERT INTO workspaces (name) VALUES (?)", (name,))
    ws_id = int(cur.lastrowid)
    add_member(conn, ws_id, owner_id, owner_email, "admin")
    conn.commit()
    return ws_id


def add_member(conn, ws_id: int, user_id: str, email: str | None, role: str = "analyst", *, commit: bool = True) -> None:
    if role not in WS_ROLES:
        role = "analyst"
    # Portable upsert: delete-then-insert keeps role current on re-invite.
    conn.execute(
        "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?", (ws_id, user_id)
    )
    conn.execute(
        "INSERT INTO workspace_members (workspace_id, user_id, email, role) VALUES (?, ?, ?, ?)",
        (ws_id, user_id, email, role),
    )
    if commit:
        conn.commit()
        invalidate_membership_cache(user_id)


def list_user_workspaces(conn, user_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT w.id, w.name, wm.role
           FROM workspace_members wm JOIN workspaces w ON w.id = wm.workspace_id
           WHERE wm.user_id = ? ORDER BY w.id""",
        (user_id,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "role": r["role"]} for r in rows]


def member_role(conn, ws_id: int, user_id: str) -> str | None:
    row = conn.execute(
        "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
        (ws_id, user_id),
    ).fetchone()
    return row["role"] if row else None


def is_member(conn, ws_id: int, user_id: str) -> bool:
    return member_role(conn, ws_id, user_id) is not None


def ensure_user_workspace(conn, user_id: str, email: str | None) -> int:
    """Every real user has at least one workspace; create a personal one on first
    login (mirrors the first-user-is-admin bootstrap)."""
    existing = list_user_workspaces(conn, user_id)
    if existing:
        return existing[0]["id"]
    name = f"{(email or 'My').split('@')[0]}'s workspace"
    return create_workspace(conn, name, user_id, email)


def resolve_workspace(conn, user_id: str, email: str | None,
                      requested: int | None, *, connection_factory=None) -> tuple[int, str]:
    """Resolve membership for 60 seconds, opening a connection only on a miss.

    Pass conn=None and a connection_factory for request middleware; callers
    with an existing connection retain ownership of it. Cache entries are
    bounded and mutations invalidate all requested-workspace keys for a user.
    """
    key = (user_id, requested)
    with _membership_lock:
        cached = _membership_cache.get(key)
        if cached is not None and cached[0] > monotonic():
            return cached[1]
    # Never wait for a pool slot while holding the cache lock: membership
    # mutations need that lock before their callers can return connections.
    owns_connection = conn is None
    if owns_connection:
        if connection_factory is None:
            from app.db import get_connection
            connection_factory = get_connection
        conn = connection_factory()
    try:
        # Another request may have filled the cache during checkout. Serialize
        # fills/provisioning and invalidation; add_member requires reentrancy.
        with _membership_lock:
            now = monotonic()
            cached = _membership_cache.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]
            _membership_cache.pop(key, None)
            result = _resolve_workspace(conn, user_id, email, requested)
            if len(_membership_cache) >= _MEMBERSHIP_CACHE_MAX:
                for stale in [k for k, entry in _membership_cache.items() if entry[0] <= now]:
                    del _membership_cache[stale]
                if len(_membership_cache) >= _MEMBERSHIP_CACHE_MAX:
                    del _membership_cache[next(iter(_membership_cache))]
            _membership_cache[key] = (monotonic() + _MEMBERSHIP_TTL, result)
            return result
    finally:
        if owns_connection:
            conn.close()


def _resolve_workspace(conn, user_id: str, email: str | None,
                      requested: int | None) -> tuple[int, str]:
    """The active workspace + the caller's role in it for a request: the requested
    one if the user is a member, else their first workspace (provisioning one if
    they have none). Returns (workspace_id, role) from a single membership read so
    the middleware doesn't need a second query for the role."""
    mine = list_user_workspaces(conn, user_id)   # [{id, name, role}]
    if requested is not None:
        for w in mine:
            if w["id"] == requested:
                return requested, w["role"]
    if mine:
        return mine[0]["id"], mine[0]["role"]
    name = f"{(email or 'My').split('@')[0]}'s workspace"
    ws = create_workspace(conn, name, user_id, email)   # creator is admin
    return ws, "admin"


def list_members(conn, ws_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT user_id, email, role, created_at FROM workspace_members
           WHERE workspace_id = ? ORDER BY created_at""",
        (ws_id,),
    ).fetchall()
    return [{"user_id": r["user_id"], "email": r["email"], "role": r["role"],
             "created_at": str(r["created_at"]) if r["created_at"] else None} for r in rows]


def create_invite(conn, ws_id: int, role: str = "analyst", email: str | None = None) -> str:
    if role not in WS_ROLES:
        role = "analyst"
    token = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT INTO workspace_invites (token, workspace_id, role, email) VALUES (?, ?, ?, ?)",
        (token, ws_id, role, email),
    )
    conn.commit()
    return token


def accept_invite(conn, token: str, user_id: str, email: str | None) -> int | None:
    row = conn.execute(
        "SELECT workspace_id, role, email, accepted FROM workspace_invites WHERE token = ?", (token,)
    ).fetchone()
    if row is None or row["accepted"]:
        return None
    if row["email"] and row["email"].strip().casefold() != (email or "").strip().casefold():
        return None
    try:
        # Claim exactly once, in the same transaction as membership creation.
        claimed = conn.execute(
            "UPDATE workspace_invites SET accepted = true WHERE token = ? AND accepted = false RETURNING workspace_id",
            (token,),
        ).fetchone()
        if claimed is None:
            conn.rollback()
            return None
        add_member(conn, int(row["workspace_id"]), user_id, email, row["role"], commit=False)
        conn.commit()
        invalidate_membership_cache(user_id)
    except Exception:
        conn.rollback()
        raise
    return int(row["workspace_id"])
