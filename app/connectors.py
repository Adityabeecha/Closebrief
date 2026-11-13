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
import re
import urllib.error
import urllib.request

from app.notifications.channels import public_urlopen, validate_public_url

KINDS = ("csv_url", "google_sheets")
_MAX_SYNC_BYTES = 10 * 1024 * 1024   # same 10MB cap as manual upload


class SyncError(Exception):
    """A sync failure whose message is safe to show: written by us, never echoing the
    source's contents or credentials."""


_DRIVE_FILE = re.compile(r"drive\.google\.com/(?:file/d/|open\?(?:[^#]*&)?id=|uc\?(?:[^#]*&)?id=)([\w-]{10,})")
_SHEET = re.compile(r"docs\.google\.com/spreadsheets/d/([\w-]{10,})")


def source_url(url: str) -> str:
    """The address to download. A Google Drive share link ('.../file/d/<id>/view') is a
    preview page, so it becomes the file's download address; a Google Sheets link becomes
    its CSV export."""
    url = (url or "").strip()
    drive = _DRIVE_FILE.search(url)
    if drive:
        return f"https://drive.google.com/uc?export=download&id={drive.group(1)}"
    sheet = _SHEET.search(url)
    if sheet:
        gid = re.search(r"[#&?]gid=(\d+)", url)
        return f"https://docs.google.com/spreadsheets/d/{sheet.group(1)}/export?format=csv&gid={gid.group(1) if gid else '0'}"
    return url


def build_url(kind: str, config: dict) -> str:
    """The fetch URL for a connector. Google Sheets becomes its CSV-export URL."""
    if kind == "csv_url":
        url = (config.get("url") or "").strip()
        if not url:
            raise ValueError("csv_url connector needs a 'url'")
        return source_url(url)
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
    try:
        with public_urlopen(req, timeout=30) as resp:
            return resp.read(_MAX_SYNC_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SyncError(f"The link isn't shared publicly (HTTP {exc.code}). Set sharing to "
                            "“Anyone with the link can view” and sync again.") from None
        if exc.code == 404:
            raise SyncError("Nothing was found at that link (HTTP 404). Check the link still opens.") from None
        raise SyncError(f"The link couldn't be downloaded (HTTP {exc.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SyncError("The link couldn't be reached. Check that it opens in a private browser window.") from None


def _read_source(raw: bytes):
    """Read a downloaded CSV or Excel file the same way an upload is read."""
    from app.ingestion.upload import UploadError, _read_frame

    head = raw[:512].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        raise SyncError("The link returned a web page, not a file. Share it as “Anyone with the link can view”; "
                        "a Google Drive file link or a Google Sheets link both work.")
    try:
        return _read_frame(raw, "source.xlsx" if raw[:2] == b"PK" else "source.csv")
    except UploadError as exc:
        raise SyncError(f"{exc}. The link must be a CSV or Excel (.xlsx) file.") from None


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
def sync_connector(conn, connector_id: int, workspace_id: int | None, *, fetch=fetch_bytes,
                   actor_id=None, actor_email=None) -> dict:
    """Fetch the source, ingest into the connector's dataset, recompute. `fetch`
    is injectable so tests run without network. Idempotent per period."""
    from app.compute.derived import refresh as _recompute_with_derived
    from app.compute.kpis import compute_and_store
    from app.datasets import (
        active_dataset_id,
        create_dataset,
        current_workspace,
        set_active,
        set_workspace_scope,
    )
    from app.import_receipts import record_receipt
    from app.ingestion.mapping import MappingError, MappingSpec, normalize, store_normalized
    from app.ingestion.profiler import profile_columns, suggest_mapping

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

    prior_scope = current_workspace()   # restore afterwards so a scheduled tick
    try:                                # doesn't leak this connector's workspace
        raw = fetch(build_url(kind, cfg))
        if len(raw) > _MAX_SYNC_BYTES:
            raise SyncError(f"The file is over the {_MAX_SYNC_BYTES // (1024 * 1024)}MB limit.")
        df = _read_source(raw)
        # The same column detection as an upload; the first good sync's mapping is kept,
        # so later syncs read the file the same way.
        mapping = cfg.get("mapping") or suggest_mapping(profile_columns(df))["mapping"]
        try:
            canonical = normalize(df, MappingSpec(**mapping))
        except (MappingError, ValueError) as exc:
            raise SyncError(f"{exc} Columns found: {', '.join(map(str, df.columns))[:300]}.") from None
        # Operate in the connector's workspace so the dataset is tenant-scoped.
        set_workspace_scope(row["workspace_id"])
        ds = conn.execute(
            "SELECT id FROM datasets WHERE name = ? AND workspace_id IS ?",
            (ds_name, row["workspace_id"]),
        ).fetchone()
        ds_id = ds["id"] if ds else create_dataset(
            conn, ds_name, activate=False, workspace_id=row["workspace_id"])
        summary = store_normalized(conn, canonical, ds_id, replace=ds is not None, source="sync",
                                   actor_id=actor_id, actor_email=actor_email)
        report = summary["import_report"]
        _recompute_with_derived(conn, ds_id, actor_id=actor_id, actor_email=actor_email, report=report)
        compute_and_store(conn, ds_id)
        if active_dataset_id(conn) is None:
            set_active(conn, ds_id)
        if not cfg.get("mapping"):
            conn.execute("UPDATE connectors SET config = ? WHERE id = ?",
                         (json.dumps({**cfg, "mapping": mapping}), connector_id))
        _finish("ok", None)
        record_receipt(conn, ds_id, kind="connector_sync", filename=row["name"], mapping=mapping,
                       report=report, written_pairs=canonical.attrs.get("written_pairs", set()))
        return {"status": "ok", "dataset_id": ds_id, "rows": summary.get("rows_normalized", 0),
                "import_report": report, "mapping": mapping}
    except SyncError as exc:
        conn.rollback()
        _finish("error", str(exc))
        return {"status": "error", "error": str(exc)}
    except Exception:  # noqa: BLE001 - upstream errors may contain credentials or source rows
        conn.rollback()
        error = "The sync failed unexpectedly. Check the link opens and is a CSV or Excel file, then sync again."
        _finish("error", error)
        return {"status": "error", "error": error}
    finally:
        set_workspace_scope(prior_scope)
