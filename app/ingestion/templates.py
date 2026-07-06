"""Mapping templates (Addendum v1.1 Section 2.2): a saved mapping + KPI
config keyed by the file's column signature, so month-2 uploads with the
same format are one click."""

import json
import sqlite3


def save_template(
    conn: sqlite3.Connection,
    column_signature: str,
    mapping: dict,
    kpi_config: dict | None = None,
) -> int:
    conn.execute(
        """
        INSERT INTO mapping_templates (column_signature, mapping, kpi_config)
        VALUES (?, ?, ?)
        ON CONFLICT(column_signature) DO UPDATE SET
            mapping=excluded.mapping, kpi_config=excluded.kpi_config
        """,
        (column_signature, json.dumps(mapping), json.dumps(kpi_config) if kpi_config else None),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM mapping_templates WHERE column_signature = ?", (column_signature,)
    ).fetchone()
    return int(row["id"])


def get_template(conn: sqlite3.Connection, template_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM mapping_templates WHERE id = ?", (template_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "column_signature": row["column_signature"],
        "mapping": json.loads(row["mapping"]),
        "kpi_config": json.loads(row["kpi_config"]) if row["kpi_config"] else None,
    }


def match_template(conn: sqlite3.Connection, column_signature: str) -> dict | None:
    row = conn.execute(
        "SELECT id FROM mapping_templates WHERE column_signature = ?", (column_signature,)
    ).fetchone()
    return get_template(conn, int(row["id"])) if row else None
