"""Dataset scoping (v1.3). A dataset is one self-contained upload: its own
metrics, values, computed facts, and KPI selections. Exactly one dataset is
active at a time; the dashboard shows only the active dataset. This is what
keeps old test uploads from leaking into a fresh import's dashboard.

Demo isolation (v3.0): a request-scoped ContextVar splits datasets into two
disjoint universes — demo (is_demo=1) and real (is_demo=0). The middleware sets
the scope from the caller's role, so demo sessions can NEVER see or activate a
real dataset, and vice-versa, enforced in SQL rather than by hiding UI.
"""

import contextvars
from datetime import datetime, timezone

from app.currency import validate_currency

# True while serving a demo (viewer) request. Default False = the real universe.
_demo_scope: contextvars.ContextVar[bool] = contextvars.ContextVar("demo_scope", default=False)
# v4.0 tenancy: the active workspace for the request (None = unscoped, e.g. tests
# and local dev — preserves single-tenant behavior). Set from verified membership.
_workspace_scope: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "workspace_scope", default=None
)
# The dataset this request asked for (X-Dataset-Id). Honoured only when it exists
# in the caller's universe/workspace; otherwise the stored default applies. This
# keeps one visitor's dataset switch from changing what everyone else sees.
_dataset_choice: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "dataset_choice", default=None
)


def set_demo_scope(is_demo: bool) -> None:
    _demo_scope.set(bool(is_demo))


def is_demo_scope() -> bool:
    """True when the current request is a demo (viewer) session. Used to keep
    cache keys from colliding across the demo/real universe boundary."""
    return _demo_scope.get()


def set_workspace_scope(workspace_id: int | None) -> None:
    _workspace_scope.set(int(workspace_id) if workspace_id is not None else None)


def current_workspace() -> int | None:
    return _workspace_scope.get()


def set_dataset_choice(dataset_id: int | None) -> None:
    _dataset_choice.set(int(dataset_id) if dataset_id is not None else None)


def _scope_pred(alias: str = "") -> str:
    # `true`/`false` literals work on both SQLite (INTEGER 0/1) and Postgres (BOOLEAN).
    p = f"{alias}." if alias else ""
    if _demo_scope.get():
        return f"{p}is_demo = true"   # demo is its own universe, workspace-agnostic
    pred = f"COALESCE({p}is_demo, false) = false"
    ws = _workspace_scope.get()
    if ws is not None:
        # ws is a server-resolved integer from workspace_members — safe to inline.
        pred += f" AND {p}workspace_id = {int(ws)}"
    return pred


def create_dataset(conn, name: str, source_upload_id: str | None = None, activate: bool = True,
                   uploaded_by: str | None = None, uploaded_by_email: str | None = None,
                   is_demo: bool = False, workspace_id: int | None = None, currency: str = "USD") -> int:
    currency = validate_currency(currency)
    # Real datasets belong to the active workspace (if any); demo datasets don't.
    ws = None if is_demo else (workspace_id if workspace_id is not None else current_workspace())
    cur = conn.execute(
        """INSERT INTO datasets (name, source_upload_id, is_active, uploaded_by,
                                 uploaded_by_email, is_demo, workspace_id, currency)
           VALUES (?, ?, 0, ?, ?, ?, ?, ?)""",
        (name, source_upload_id, uploaded_by, uploaded_by_email, bool(is_demo), ws, currency),
    )
    dataset_id = int(cur.lastrowid)
    if activate:
        # Activate within the dataset's own universe.
        prev = _demo_scope.get()
        _demo_scope.set(bool(is_demo))
        try:
            set_active(conn, dataset_id)
        finally:
            _demo_scope.set(prev)
    conn.commit()
    return dataset_id


def set_active(conn, dataset_id: int) -> None:
    # Only reset the active flag within the current universe, so activating a
    # demo dataset never deactivates the real one (or vice-versa).
    conn.execute(f"UPDATE datasets SET is_active = 0 WHERE {_scope_pred()}")
    conn.execute("UPDATE datasets SET is_active = 1 WHERE id = ?", (dataset_id,))
    conn.commit()


def active_dataset_id(conn) -> int | None:
    chosen = _dataset_choice.get()
    if chosen is not None:
        row = conn.execute(
            f"SELECT id FROM datasets WHERE id = ? AND {_scope_pred()}", (chosen,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
    row = conn.execute(
        f"SELECT id FROM datasets WHERE is_active = 1 AND {_scope_pred()} ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        # Fall back to the newest dataset in this universe (demo has exactly one).
        row = conn.execute(
            f"SELECT id FROM datasets WHERE {_scope_pred()} ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return int(row["id"]) if row else None


def list_datasets(conn) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT d.id, d.name, d.source_upload_id, d.is_active, d.created_at, d.domain, d.currency, d.fiscal_year_start,
               d.reason_threshold_pct, d.reason_threshold_abs, d.data_changed_at, d.amount_scale, d.import_pending,
               (SELECT COUNT(*) FROM metrics m WHERE m.dataset_id = d.id) AS metric_count
        FROM datasets d WHERE {_scope_pred('d')} ORDER BY d.id DESC
        """
    ).fetchall()
    active = active_dataset_id(conn)
    return [
        {
            "id": r["id"], "name": r["name"], "source_upload_id": r["source_upload_id"],
            "is_active": r["id"] == active, "created_at": str(r["created_at"]),
            "domain": r["domain"] or "fpa", "currency": r["currency"],
            "fiscal_year_start": r["fiscal_year_start"],
            "reason_threshold_pct": float(r["reason_threshold_pct"]) if r["reason_threshold_pct"] is not None else 10.0,
            "reason_threshold_abs": float(r["reason_threshold_abs"]) if r["reason_threshold_abs"] is not None else None,
            "amount_scale": int(r["amount_scale"] or 1),
            "import_pending": bool(r["import_pending"]),
            "data_changed_at": timestamp_text(r["data_changed_at"]),
            "metric_count": r["metric_count"],
        }
        for r in rows
    ]


def delete_dataset(conn, dataset_id: int) -> bool:
    """Remove a dataset and everything scoped to it (facts, values, reports,
    bridges, KPI configs, metrics, version history and jobs). Reactivates the newest remaining dataset."""
    row = conn.execute(
        f"SELECT id FROM datasets WHERE id = ? AND {_scope_pred()}", (dataset_id,)
    ).fetchone()
    if row is None:
        return False

    metric_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM metrics WHERE dataset_id = ?", (dataset_id,)
        ).fetchall()
    ]
    for mid in metric_ids:
        report_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM generated_reports WHERE metric_id = ?", (mid,)
            ).fetchall()
        ]
        for rid in report_ids:
            conn.execute("DELETE FROM report_versions WHERE report_id = ?", (rid,))
            conn.execute("DELETE FROM feedback WHERE report_id = ?", (rid,))
        conn.execute("DELETE FROM generated_reports WHERE metric_id = ?", (mid,))
        conn.execute("DELETE FROM computed_facts WHERE metric_id = ?", (mid,))
        conn.execute("DELETE FROM metric_values WHERE metric_id = ?", (mid,))
        conn.execute("DELETE FROM pvm_bridges WHERE metric_id = ?", (mid,))
        conn.execute("DELETE FROM metric_breakdowns WHERE metric_id = ?", (mid,))
    # Notes written for this dataset go with it; workspace-wide notes stay.
    conn.execute("DELETE FROM context_documents WHERE dataset_id = ?", (dataset_id,))
    conn.execute(
        "DELETE FROM scheduler_runs WHERE job_id IN (SELECT id FROM scheduled_jobs WHERE dataset_id = ?)",
        (dataset_id,),
    )
    for table in ("scheduled_jobs", "digest_runs", "derived_metrics", "import_receipts", "value_changes", "close_locks"):
        conn.execute(f"DELETE FROM {table} WHERE dataset_id = ?", (dataset_id,))
    conn.execute("DELETE FROM kpi_configs WHERE dataset_id = ?", (dataset_id,))
    conn.execute("DELETE FROM metrics WHERE dataset_id = ?", (dataset_id,))
    was_active = conn.execute(
        "SELECT is_active FROM datasets WHERE id = ?", (dataset_id,)
    ).fetchone()["is_active"]
    conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
    conn.commit()

    if was_active:
        newest = conn.execute(
            f"SELECT id FROM datasets WHERE {_scope_pred()} ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if newest:
            set_active(conn, int(newest["id"]))
    return True


def get_or_create_metric(conn, dataset_id: int, name: str, external_id: str | None = None) -> int:
    """Metric ids are per-dataset, so the same name in two datasets is two
    distinct rows. Enforced in code AND by UNIQUE(dataset_id, name).

    external_id (a GL code, SKU, ...) is recorded on first creation and
    backfilled onto an existing row that doesn't have one yet — it's never
    overwritten once set, so a later upload with a different or missing id
    can't clobber a value another upload already established."""
    row = conn.execute(
        "SELECT id, external_id FROM metrics WHERE dataset_id = ? AND name = ?", (dataset_id, name)
    ).fetchone()
    if row is not None:
        if external_id and not row["external_id"]:
            conn.execute("UPDATE metrics SET external_id = ? WHERE id = ?", (external_id, row["id"]))
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO metrics (dataset_id, name, external_id) VALUES (?, ?, ?)",
        (dataset_id, name, external_id),
    )
    return int(cur.lastrowid)


def dataset_currency(conn, dataset_id: int | None) -> str:
    """Currency for an already scope-checked dataset (USD for an empty workspace)."""
    return dataset_metadata(conn, dataset_id)["currency"]


def dataset_metadata(conn, dataset_id: int | None) -> dict:
    """Display metadata for an already scope-checked dataset."""
    row = conn.execute(
        "SELECT currency, fiscal_year_start, reason_threshold_pct, reason_threshold_abs, data_changed_at, amount_scale "
        "FROM datasets WHERE id = ?", (dataset_id,),
    ).fetchone() if dataset_id else None
    return {"currency": row["currency"] if row else "USD", "fiscal_year_start": row["fiscal_year_start"] if row else 1,
            "reason_threshold_pct": float(row["reason_threshold_pct"]) if row and row["reason_threshold_pct"] is not None else 10.0,
            "reason_threshold_abs": (float(row["reason_threshold_abs"])
                                     if row and row["reason_threshold_abs"] is not None else None),
            "data_changed_at": timestamp_text(row["data_changed_at"]) if row else None,
            "amount_scale": int(row["amount_scale"] or 1) if row else 1}


def timestamp_text(value) -> str | None:
    """Consistent UTC timestamps for SQLite text and Postgres datetime values."""
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def mark_data_changed(conn, dataset_id: int) -> None:
    """Call within the value-writing transaction; metadata edits don't call this."""
    conn.execute("UPDATE datasets SET data_changed_at = ? WHERE id = ?",
                 (datetime.now(timezone.utc).isoformat(), dataset_id))
