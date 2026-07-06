"""Dataset scoping (v1.3). A dataset is one self-contained upload: its own
metrics, values, computed facts, and KPI selections. Exactly one dataset is
active at a time; the dashboard shows only the active dataset. This is what
keeps old test uploads from leaking into a fresh import's dashboard.
"""



def create_dataset(conn, name: str, source_upload_id: str | None = None, activate: bool = True,
                   uploaded_by: str | None = None, uploaded_by_email: str | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO datasets (name, source_upload_id, is_active, uploaded_by, uploaded_by_email)
           VALUES (?, ?, 0, ?, ?)""",
        (name, source_upload_id, uploaded_by, uploaded_by_email),
    )
    dataset_id = int(cur.lastrowid)
    if activate:
        set_active(conn, dataset_id)
    conn.commit()
    return dataset_id


def set_active(conn, dataset_id: int) -> None:
    conn.execute("UPDATE datasets SET is_active = 0")
    conn.execute("UPDATE datasets SET is_active = 1 WHERE id = ?", (dataset_id,))
    conn.commit()


def active_dataset_id(conn) -> int | None:
    row = conn.execute(
        "SELECT id FROM datasets WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return int(row["id"]) if row else None


def list_datasets(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT d.id, d.name, d.source_upload_id, d.is_active, d.created_at,
               (SELECT COUNT(*) FROM metrics m WHERE m.dataset_id = d.id) AS metric_count
        FROM datasets d ORDER BY d.id DESC
        """
    ).fetchall()
    return [
        {
            "id": r["id"], "name": r["name"], "source_upload_id": r["source_upload_id"],
            "is_active": bool(r["is_active"]), "created_at": str(r["created_at"]),
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
            "SELECT id FROM datasets ORDER BY id DESC LIMIT 1"
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
