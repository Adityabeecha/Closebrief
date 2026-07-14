"""Notification channel configs (email / Slack / webhook) + a delivery test.

Admin-only. The rich Slack/email payloads live in app/notifications/*."""

import json

from fastapi import APIRouter, Depends, HTTPException

from app.api import CurrentUser, require_admin
from app.db import get_connection
from app.notifications.channels import NotificationError, make_channel
from app.notifications.scheduler import list_configs as notif_list_configs

router = APIRouter(tags=["notifications"])


@router.get("/notifications/configs")
def list_notification_configs(_: CurrentUser = Depends(require_admin)) -> list[dict]:
    conn = get_connection()
    try:
        return notif_list_configs(conn)
    finally:
        conn.close()


@router.post("/notifications/configs", status_code=201)
def create_notification_config(payload: dict, _: CurrentUser = Depends(require_admin)) -> dict:
    channel = payload.get("channel")
    if channel not in ("email", "slack", "webhook"):
        raise HTTPException(status_code=422, detail="channel must be email|slack|webhook")
    config = payload.get("config") or {}
    # Use a Python bool: psycopg maps it to Postgres BOOLEAN, sqlite3 to 0/1.
    enabled = bool(payload.get("enabled", True))
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO notification_configs (channel, config, enabled) VALUES (?, ?, ?)",
            (channel, json.dumps(config), enabled),
        )
        conn.commit()
        new_id = cur.lastrowid if hasattr(cur, "lastrowid") and cur.lastrowid else conn.execute(
            "SELECT MAX(id) AS id FROM notification_configs"
        ).fetchone()["id"]
        return {"id": new_id, "channel": channel, "config": config, "enabled": bool(enabled)}
    finally:
        conn.close()


@router.put("/notifications/{config_id}")
def update_notification_config(config_id: int, payload: dict, _: CurrentUser = Depends(require_admin)) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM notification_configs WHERE id = ?", (config_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="config not found")
        config = payload.get("config")
        enabled = payload.get("enabled")
        if config is not None:
            conn.execute(
                "UPDATE notification_configs SET config = ? WHERE id = ?",
                (json.dumps(config), config_id),
            )
        if enabled is not None:
            conn.execute(
                "UPDATE notification_configs SET enabled = ? WHERE id = ?",
                (bool(enabled), config_id),
            )
        conn.commit()
        return {"id": config_id, "updated": True}
    finally:
        conn.close()


@router.delete("/notifications/{config_id}", status_code=204)
def delete_notification_config(config_id: int, _: CurrentUser = Depends(require_admin)) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM notification_configs WHERE id = ?", (config_id,))
        conn.commit()
    finally:
        conn.close()


@router.post("/notifications/test/{config_id}")
def test_notification_config(config_id: int, _: CurrentUser = Depends(require_admin)) -> dict:
    """Send a sample anomaly alert through one config to verify delivery."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT channel, config FROM notification_configs WHERE id = ?", (config_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="config not found")
        sample = [{
            "metric": "Net Revenue", "period": "2025-03",
            "value": "$5.33M", "delta": "+27.6% vs plan",
            "narrative": "This is a Closebrief test notification.",
        }]
        try:
            make_channel(row["channel"], json.loads(row["config"] or "{}")).send_anomaly_alert(sample)
        except NotificationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001 - surface delivery failures to the caller
            raise HTTPException(status_code=502, detail=f"Delivery failed: {e}") from e
        return {"ok": True}
    finally:
        conn.close()
