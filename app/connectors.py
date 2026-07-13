"""Live data connectors (v4.0). Replace manual CSV upload with scheduled syncs
from a source. Free-tier connectors need no paid account:

  csv_url        — fetch a CSV from any https URL           config: {"url": ...}
  google_sheets  — a shared/public Google Sheet as CSV      config: {"sheet_id": ..., "gid": "0"}

A sync fetches CSV bytes, runs them through the normal ingestion pipeline into the
connector's dataset (idempotent: metric_values upsert by period), and recomputes.
Snowflake/BigQuery/QuickBooks are the same shape plus auth — added later.
"""

from __future__ import annotations

import json
import urllib.request

from app.notifications.channels import validate_public_url

KINDS = ("csv_url", "google_sheets")
_MAX_SYNC_BYTES = 10 * 1024 * 1024   # same 10MB cap as manual upload


def build_url(kind: str, config: dict) -> str:
    """The fetch URL for a connector. Google Sheets becomes its CSV-export URL."""
    if kind == "csv_url":
        url = (config.get("url") or "").strip()
        if not url:
            raise ValueError("csv_url connector needs a 'url'")
        return url
    if kind == "google_sheets":
        sheet_id = (config.get("sheet_id") or "").strip()
        if not sheet_id:
            raise ValueError("google_sheets connector needs a 'sheet_id'")
        gid = (config.get("gid") or "0").strip()
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    raise ValueError(f"Unknown connector kind: {kind}")


def fetch_bytes(url: str) -> bytes:
    """GET the URL with an SSRF guard (no private/loopback/metadata hosts) and a
    size cap. Google's export URL redirects; urllib follows it."""
    validate_public_url(url)   # reuse the webhook SSRF guard
    req = urllib.request.Request(url, headers={"User-Agent": "Closebrief-Connector/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - operator-configured, guarded
        return resp.read(_MAX_SYNC_BYTES + 1)


# ---------------------------------------------------------------- CRUD
def list_connectors(conn, workspace_id: int | None) -> list[dict]:
    rows = conn.execute(
        "SELECT id, workspace_id, kind, name, config, dataset_name, enabled, "
        "last_sync_at, last_status, last_error FROM connectors "
        "WHERE workspace_id IS ? OR workspace_id = ? ORDER BY id",
        (workspace_id, workspace_id),
    ).fetchall()
    out = []
    for r in rows:
        cfg = json.loads(r["config"]) if r["config"] else {}
        cfg.pop("url", None)   # don't echo a possibly-sensitive source URL back wholesale
        out.append({
            "id": r["id"], "kind": r["kind"], "name": r["name"], "config": cfg,
            "dataset_name": r["dataset_name"], "enabled": bool(r["enabled"]),
            "last_sync_at": str(r["last_sync_at"]) if r["last_sync_at"] else None,
            "last_status": r["last_status"], "last_error": r["last_error"],
        })
    return out


def create_connector(conn, kind: str, name: str, config: dict, dataset_name: str,
                     workspace_id: int | None) -> int:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    build_url(kind, config)   # validate config now, fail fast on a bad connector
    cur = conn.execute(
        """INSERT INTO connectors (workspace_id, kind, name, config, dataset_name)
           VALUES (?, ?, ?, ?, ?)""",
        (workspace_id, kind, name, json.dumps(config), dataset_name or name),
    )
    conn.commit()
    return int(cur.lastrowid)


def delete_connector(conn, connector_id: int, workspace_id: int | None) -> bool:
    row = conn.execute(
        "SELECT 1 FROM connectors WHERE id = ? AND (workspace_id IS ? OR workspace_id = ?)",
        (connector_id, workspace_id, workspace_id),
    ).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM connectors WHERE id = ?", (connector_id,))
    conn.commit()
    return True


def _get(conn, connector_id: int, workspace_id: int | None):
    return conn.execute(
        "SELECT * FROM connectors WHERE id = ? AND (workspace_id IS ? OR workspace_id = ?)",
        (connector_id, workspace_id, workspace_id),
    ).fetchone()


def all_enabled(conn) -> list[tuple[int, int | None]]:
    """(id, workspace_id) for every enabled connector — used by the scheduler to
    sync each in its own workspace scope."""
    rows = conn.execute(
        "SELECT id, workspace_id FROM connectors WHERE enabled = true ORDER BY id"
    ).fetchall()
    return [(r["id"], r["workspace_id"]) for r in rows]


# ---------------------------------------------------------------- sync
def sync_connector(conn, connector_id: int, workspace_id: int | None, *, fetch=fetch_bytes) -> dict:
    """Fetch the source, ingest into the connector's dataset, recompute. `fetch`
    is injectable so tests run without network. Idempotent per period."""
    from app.compute.kpis import compute_and_store
    from app.datasets import (
        active_dataset_id,
        create_dataset,
        set_active,
        set_workspace_scope,
    )
    from app.ingestion.ingest import ingest_dataframe, parse_csv

    row = _get(conn, connector_id, workspace_id)
    if row is None:
        raise ValueError("connector not found")
    kind, cfg = row["kind"], (json.loads(row["config"]) if row["config"] else {})
    ds_name = row["dataset_name"] or row["name"]

    def _finish(status: str, error: str | None):
        conn.execute(
            "UPDATE connectors SET last_status = ?, last_error = ?, last_sync_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, error, connector_id),
        )
        conn.commit()

    try:
        raw = fetch(build_url(kind, cfg))
        if len(raw) > _MAX_SYNC_BYTES:
            raise ValueError(f"source exceeds the {_MAX_SYNC_BYTES // (1024 * 1024)}MB limit")
        df = parse_csv(raw)
        # Operate in the connector's workspace so the dataset is tenant-scoped.
        set_workspace_scope(row["workspace_id"])
        ds = conn.execute(
            "SELECT id FROM datasets WHERE name = ? AND workspace_id IS ?",
            (ds_name, row["workspace_id"]),
        ).fetchone()
        ds_id = ds["id"] if ds else create_dataset(
            conn, ds_name, activate=False, workspace_id=row["workspace_id"])
        ingest_dataframe(conn, df, ds_id)
        compute_and_store(conn, ds_id)
        if active_dataset_id(conn) is None:
            set_active(conn, ds_id)
        _finish("ok", None)
        return {"status": "ok", "dataset_id": ds_id, "rows": len(df)}
    except Exception as e:  # noqa: BLE001 - report per-connector, never crash the tick
        _finish("error", str(e)[:300])
        return {"status": "error", "error": str(e)}
