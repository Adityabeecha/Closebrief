"""Immutable receipts for the particular metric/month rows an operation wrote."""

import json
import logging

logger = logging.getLogger(__name__)


def _kpi_totals(conn, dataset_id: int, written_pairs) -> list[dict]:
    pairs = set(written_pairs)
    totals = {}
    for row in conn.execute(
        """SELECT m.name, mv.period, mv.value, mv.budget, mv.source_rows
           FROM metric_values mv JOIN metrics m ON m.id = mv.metric_id
           WHERE m.dataset_id = ? ORDER BY m.name, mv.period""", (dataset_id,),
    ).fetchall():
        if (row["name"], row["period"]) not in pairs:
            continue
        item = totals.setdefault(row["name"], {
            "metric": row["name"], "periods": 0, "value_total": 0.0, "budget_total": None, "rows": 0,
        })
        item["periods"] += 1
        item["value_total"] += float(row["value"])
        if row["budget"] is not None:
            item["budget_total"] = (item["budget_total"] or 0.0) + float(row["budget"])
        item["rows"] += int(row["source_rows"])
    return list(totals.values())


def record_receipt(conn, dataset_id: int, *, kind: str, filename: str,
                   mapping: dict, report: dict, written_pairs) -> None:
    """Best effort after the data operation commits; a receipt failure is isolated.

    Totals snapshot the resulting stored rows for just this operation's keys.
    A budget join therefore shows existing actuals and the newly joined budget,
    with source-row counts from those stored actual observations.
    """
    savepoint = False
    try:
        conn.execute("SAVEPOINT import_receipt")
        savepoint = True
        dataset = conn.execute("SELECT workspace_id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        conn.execute(
            """INSERT INTO import_receipts (dataset_id, workspace_id, kind, filename, mapping, report, kpi_totals)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (dataset_id, dataset["workspace_id"], kind, filename, json.dumps(mapping), json.dumps(report),
             json.dumps(_kpi_totals(conn, dataset_id, written_pairs))),
        )
        conn.execute("RELEASE SAVEPOINT import_receipt")
        savepoint = False
        conn.commit()
    except Exception:
        logger.exception("Could not save import receipt for dataset %s", dataset_id)
        try:
            if savepoint:
                conn.execute("ROLLBACK TO SAVEPOINT import_receipt")
                conn.execute("RELEASE SAVEPOINT import_receipt")
            else:
                conn.rollback()
        except Exception:
            logger.exception("Could not recover receipt transaction for dataset %s", dataset_id)
            try:
                conn.rollback()
            except Exception:
                logger.exception("Could not roll back receipt connection for dataset %s", dataset_id)


def list_receipts(conn, dataset_id: int) -> list[dict]:
    """Read receipts only after the caller has scope-checked the dataset."""
    return [
        {"id": row["id"], "kind": row["kind"], "filename": row["filename"], "created_at": str(row["created_at"]),
         "mapping": json.loads(row["mapping"]), "report": json.loads(row["report"]), "kpi_totals": json.loads(row["kpi_totals"])}
        for row in conn.execute(
            "SELECT * FROM import_receipts WHERE dataset_id = ? ORDER BY created_at DESC, id DESC", (dataset_id,),
        ).fetchall()
    ]
