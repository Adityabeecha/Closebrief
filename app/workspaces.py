"""Workspace membership & invites (v4.0 multi-tenancy).

A workspace is a tenant; membership gates which workspace's data a user may
scope to. The active workspace is resolved server-side from verified membership
(never trusted from the client). Roles are per-workspace here.
"""

from __future__ import annotations

import secrets

WS_ROLES = ("admin", "analyst", "executive")


def create_workspace(conn, name: str, owner_id: str, owner_email: str | None = None) -> int:
    cur = conn.execute("INSERT INTO workspaces (name) VALUES (?)", (name,))
    ws_id = int(cur.lastrowid)
    add_member(conn, ws_id, owner_id, owner_email, "admin")
    conn.commit()
    return ws_id


def add_member(conn, ws_id: int, user_id: str, email: str | None, role: str = "analyst") -> None:
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
    conn.commit()


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
        "SELECT workspace_id, role, accepted FROM workspace_invites WHERE token = ?", (token,)
    ).fetchone()
    if row is None or row["accepted"]:
        return None
    add_member(conn, int(row["workspace_id"]), user_id, email, row["role"])
    conn.execute("UPDATE workspace_invites SET accepted = 1 WHERE token = ?", (token,))
    conn.commit()
    return int(row["workspace_id"])
