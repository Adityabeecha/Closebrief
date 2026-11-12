"""SQLite upgrades ignore only the expected idempotency errors."""

import sqlite3

import pytest

from app import db


@pytest.mark.parametrize("source_rows", [None, 3])
def test_data_copy_preserves_legacy_and_new_source_row_counts(source_rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        schema = db._SQLITE_SCHEMA
        if source_rows is None:
            schema = schema.replace("    source_rows INTEGER NOT NULL DEFAULT 1,\n", "")
        conn.executescript(schema)
        conn.execute("INSERT INTO metrics (name) VALUES ('Revenue')")
        conn.execute("INSERT INTO metric_values (metric_id, period, value) VALUES (1, '2025-01', 123)")
        if source_rows:
            conn.execute("UPDATE metric_values SET source_rows = ?", (source_rows,))
        cols = dict(db._COPY_TABLES)["metric_values"]
        rows = db._sqlite_copy_rows(conn, "metric_values", cols)
        assert len(rows) == 1
        assert rows[0]["value"] == 123
        assert rows[0]["source_rows"] == (source_rows or 1)
    finally:
        conn.close()


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "migration.db"))
    return monkeypatch


@pytest.mark.parametrize("statement,message", [
    ("ALTER TABLE missing_table ADD COLUMN bad TEXT", "no such table"),
    ("ALTR TABLE metrics ADD COLUMN bad TEXT", "syntax error"),
    ("CREATE INDEX broken_index ON metrics(missing_column)", "no such column"),
    ("ALTER TABLE metrics RENAME TO workspaces", "already"),
])
def test_unexpected_migration_error_is_not_swallowed(local_db, statement, message):
    local_db.setattr(db, "_SQLITE_MIGRATIONS", [*db._SQLITE_MIGRATIONS, statement])
    with pytest.raises(sqlite3.OperationalError, match=message):
        db.init_db()


def test_valid_alter_can_be_rerun_without_losing_data(local_db):
    local_db.setattr(db, "_SQLITE_MIGRATIONS", [*db._SQLITE_MIGRATIONS,
                                             "ALTER TABLE metrics ADD COLUMN migration_probe TEXT"])
    db.init_db()
    conn = db.get_connection()
    try:
        conn.execute("INSERT INTO metrics (name, migration_probe) VALUES ('Revenue', 'kept')")
        conn.commit()
    finally:
        conn.close()
    db.init_db()
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT migration_probe FROM metrics").fetchone()["migration_probe"] == "kept"
    finally:
        conn.close()


def test_existing_index_is_ignored_when_create_is_rerun(local_db):
    local_db.setattr(db, "_SQLITE_MIGRATIONS", [*db._SQLITE_MIGRATIONS,
                                             "CREATE INDEX migration_probe_index ON metrics(name)"])
    db.init_db()
    db.init_db()
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='migration_probe_index'").fetchone()
    finally:
        conn.close()
