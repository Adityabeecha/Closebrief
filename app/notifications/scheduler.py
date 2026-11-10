"""Notification scheduling helpers (v2.1).

Kept intentionally simple and *not* auto-started, so a deploy can never hang on a
background loop. `deliver` fans one payload out to all enabled configs whose
subscribed events include the given event; the API's test endpoint and any future
cron/worker call the same path.
"""

from __future__ import annotations

import json

from app.notifications.channels import make_channel


def list_configs(conn, workspace_id: int | None) -> list[dict]:
    rows = conn.execute(
        "SELECT id, channel, config, enabled, created_at FROM notification_configs WHERE workspace_id IS ? ORDER BY id",
        (workspace_id,),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "channel": r["channel"],
            "config": json.loads(r["config"]) if r["config"] else {},
            "enabled": bool(r["enabled"]),
            "created_at": r["created_at"],
        })
    return out


def deliver(conn, event: str, *, workspace_id: int | None, period: str | None = None, items: list[dict] | None = None) -> dict:
    """Send `event` (digest_generated | anomaly_detected) to enabled configs in the given workspace
    subscribed to it. NULL configs belong only to the unscoped local-dev scope. Returns {sent: [...], failed: [{id, error}]}."""
    items = items or []
    sent, failed = [], []
    for cfg in list_configs(conn, workspace_id):
        if not cfg["enabled"]:
            continue
        events = cfg["config"].get("events")
        if events and event not in events:
            continue
        try:
            ch = make_channel(cfg["channel"], cfg["config"])
            if event == "anomaly_detected":
                ch.send_anomaly_alert(items)
            else:
                ch.send_digest(period or "", items)
            sent.append(cfg["id"])
        except Exception as e:  # noqa: BLE001 - report per-config, don't abort the fan-out
            failed.append({"id": cfg["id"], "error": str(e)})
    return {"sent": sent, "failed": failed}
