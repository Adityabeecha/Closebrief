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

# True while serving a demo (viewer) request. Default False = the real universe.
_demo_scope: contextvars.ContextVar[bool] = contextvars.ContextVar("demo_scope", default=False)


def set_demo_scope(is_demo: bool) -> None:
    _demo_scope.set(bool(is_demo))


def _scope_pred(alias: str = "") -> str:
    # `true`/`false` literals work on both SQLite (INTEGER 0/1) and Postgres (BOOLEAN).
    p = f"{alias}." if alias else ""
    return f"{p}is_demo = true" if _demo_scope.get() else f"COALESCE({p}is_demo, false) = false"


def create_dataset(conn, name: str, source_upload_id: str | None = None, activate: bool = True,
                   uploaded_by: str | None = None, uploaded_by_email: str | None = None,
                   is_demo: bool = False) -> int:
    cur = conn.execute(
        """INSERT INTO datasets (name, source_upload_id, is_active, uploaded_by, uploaded_by_email, is_demo)
           VALUES (?, ?, 0, ?, ?, ?)""",
        (name, source_upload_id, uploaded_by, uploaded_by_email, bool(is_demo)),
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
        SELECT d.id, d.name, d.source_upload_id, d.is_active, d.created_at, d.domain,
               (SELECT COUNT(*) FROM metrics m WHERE m.dataset_id = d.id) AS metric_count
        FROM datasets d WHERE {_scope_pred('d')} ORDER BY d.id DESC
        """
    ).fetchall()
    return [
        {
            "id": r["id"], "name": r["name"], "source_upload_id": r["source_upload_id"],
            "is_active": bool(r["is_active"]), "created_at": str(r["created_at"]),
            "domain": r["domain"] or "fpa",
            "metric_count": r["metric_count"],
        }
        for r in rows
    ]


def delete_dataset(conn, dataset_id: int) -> bool:
    """Remove a dataset and everything scoped to it (facts, values, reports,
    bridges, KPI configs, metrics). Reactivates the newest remaining dataset."""
    row = conn.execute("SELECT id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
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
            conn.execute("DELETE FROM feedback WHERE report_id = ?", (rid,))
        conn.execute("DELETE FROM generated_reports WHERE metric_id = ?", (mid,))
        conn.execute("DELETE FROM computed_facts WHERE metric_id = ?", (mid,))
        conn.execute("DELETE FROM metric_values WHERE metric_id = ?", (mid,))
        conn.execute("DELETE FROM pvm_bridges WHERE metric_id = ?", (mid,))
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


def get_or_create_metric(conn, dataset_id: int, name: str) -> int:
    """Metric ids are per-dataset, so the same name in two datasets is two
    distinct rows. Enforced in code AND by UNIQUE(dataset_id, name)."""
    row = conn.execute(
        "SELECT id FROM metrics WHERE dataset_id = ? AND name = ?", (dataset_id, name)
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO metrics (dataset_id, name) VALUES (?, ?)", (dataset_id, name)
    )
    return int(cur.lastrowid)
