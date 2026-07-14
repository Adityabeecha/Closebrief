"""Admin: application-role management (v1.2 Phase 2)."""

from fastapi import APIRouter, Depends, HTTPException

from app.api import CurrentUser, require_admin
from app.auth import invalidate_role_cache
from app.db import get_connection

router = APIRouter(tags=["admin"])


@router.get("/admin/users")
def admin_list_users(_: CurrentUser = Depends(require_admin)) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT user_id, email, role, created_at, updated_at FROM app_roles ORDER BY created_at"
        ).fetchall()
        return [
            {"user_id": str(r["user_id"]), "email": r["email"], "role": r["role"],
             "created_at": str(r["created_at"]), "updated_at": str(r["updated_at"])}
            for r in rows
        ]
    finally:
        conn.close()


@router.put("/admin/users/{user_id}/role")
def admin_set_role(user_id: str, role: str, current: CurrentUser = Depends(require_admin)) -> dict:
    # "viewer" is reserved for anonymous demo sessions — assigning it to a real
    # account would demo-scope them (they'd see only the demo dataset).
    assignable = ("analyst", "executive", "admin")
    if role not in assignable:
        raise HTTPException(status_code=422, detail=f"role must be one of {assignable}")
    if user_id == current.id and role != "admin":
        raise HTTPException(status_code=400, detail="You cannot demote yourself")
    conn = get_connection()
    try:
        # A malformed id (e.g. not a UUID) makes Postgres raise on the cast; treat
        # that as "not found" rather than leaking a 500.
        try:
            row = conn.execute(
                "SELECT 1 FROM app_roles WHERE user_id = ?", (user_id,)
            ).fetchone()
        except Exception:  # noqa: BLE001 - invalid id -> 404, not 500
            conn.rollback()
            row = None
        if not row:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        conn.execute(
            "UPDATE app_roles SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (role, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    invalidate_role_cache(user_id)  # so the change takes effect immediately
    return {"user_id": user_id, "role": role}
