import io
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from dbharness import use_test_db

from app import db, retention
from app.config import settings
from app.datasets import set_workspace_scope
from app.ingestion import upload


@pytest.fixture
def conn(tmp_path, monkeypatch):
    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "uploads.db"))
    monkeypatch.chdir(tmp_path)
    set_workspace_scope(None)
    db.init_db()
    connection = db.get_connection()
    yield connection
    connection.close()
    set_workspace_scope(None)


def test_upload_survives_reopening_without_disk_directory(conn):
    raw = b"Account,Amount\nRevenue,120\n"
    result = upload.create_upload(conn, raw, "finance.csv")
    assert not upload.UPLOAD_DIR.exists()
    row = upload.get_upload(conn, result["upload_id"])
    assert bytes(row["content"]) == raw
    assert row["file_path"] is None
    reopened = db.get_connection()
    try:
        frame = upload.load_upload_frame(reopened, result["upload_id"])
        assert frame.to_dict("records") == [{"Account": "Revenue", "Amount": "120"}]
    finally:
        reopened.close()


def test_legacy_file_upload_still_loads(conn, tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_bytes(b"Account,Amount\nRevenue,42\n")
    conn.execute("INSERT INTO uploads (id, filename, file_path) VALUES (?, ?, ?)",
                 ("legacy", "finance.csv", str(path)))
    conn.commit()
    assert upload.load_upload_frame(conn, "legacy").iloc[0]["Amount"] == "42"


def test_workbook_period_reads_database_content(conn):
    stream = io.BytesIO()
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        pd.DataFrame([["Account", "Amount"], ["Revenue", "120"]]).to_excel(
            writer, sheet_name="AR Jun-25", header=False, index=False,
        )
    result = upload.create_upload(conn, stream.getvalue(), "finance.xlsx")
    assert upload.upload_sheet_period(conn, result["upload_id"]) == "2025-06"
    assert not upload.UPLOAD_DIR.exists()


def test_content_takes_precedence_over_legacy_path(conn, tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_bytes(b"Account,Amount\nRevenue,999\n")
    result = upload.create_upload(conn, b"Account,Amount\nRevenue,120\n", "finance.csv")
    conn.execute("UPDATE uploads SET file_path = ? WHERE id = ?", (str(path), result["upload_id"]))
    conn.commit()
    assert upload.load_upload_frame(conn, result["upload_id"]).iloc[0]["Amount"] == "120"


@pytest.mark.parametrize("days", [7, 30])
def test_purge_removes_only_expired_uploads_and_legacy_files(conn, tmp_path, days):
    now = datetime.now(timezone.utc)
    expired = (now - timedelta(days=days + 1)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d %H:%M:%S")
    for name, created_at, legacy in [
        ("old_blob", expired, False), ("old_file", expired, True),
        ("old_missing", expired, True), ("recent_blob", recent, False),
        ("recent_file", recent, True),
    ]:
        path = tmp_path / f"{name}.csv"
        if legacy and name != "old_missing":
            path.write_bytes(b"A\n1\n")
        conn.execute("INSERT INTO uploads (id, filename, file_path, created_at) VALUES (?, ?, ?, ?)",
                     (name, "finance.csv", str(path) if legacy else None, created_at))
    conn.commit()
    assert upload.purge_old_uploads(conn, days=days) == 3
    assert {r["id"] for r in conn.execute("SELECT id FROM uploads").fetchall()} == {"recent_blob", "recent_file"}
    assert not (tmp_path / "old_file.csv").exists()
    assert (tmp_path / "recent_file.csv").exists()
    assert upload.purge_old_uploads(conn, days=days) == 0


def test_retention_purges_uploads_without_workspace_policy(conn):
    conn.execute("INSERT INTO uploads (id, filename, file_path, created_at) "
                 "VALUES ('expired', 'finance.csv', NULL, '2020-01-01 00:00:00')")
    conn.commit()
    result = retention.run_retention(conn)
    assert result["uploads_purged"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM uploads").fetchone()["n"] == 0


@pytest.mark.sqlite_only
def test_sqlite_upgrade_preserves_legacy_upload_and_accepts_blob(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "legacy.db"))
    monkeypatch.chdir(tmp_path)
    set_workspace_scope(None)
    path = tmp_path / "old.csv"
    path.write_bytes(b"A\n1\n")
    legacy = sqlite3.connect(settings.db_path)
    schema = db._SQLITE_SCHEMA.replace("    content BLOB,\n", "").replace("    file_path TEXT,", "    file_path TEXT NOT NULL,")
    legacy.executescript(schema)
    legacy.execute("INSERT INTO uploads (id, filename, file_path) VALUES ('old', 'old.csv', ?)", (str(path),))
    legacy.commit()
    legacy.close()
    db.init_db()
    db.init_db()  # upgrades must be idempotent
    upgraded = db.get_connection()
    try:
        assert upload.load_upload_frame(upgraded, "old").iloc[0]["A"] == "1"
        result = upload.create_upload(upgraded, b"A\n2\n", "new.csv")
        assert upload.get_upload(upgraded, result["upload_id"])["file_path"] is None
    finally:
        upgraded.close()


def test_scheduler_tick_expires_uploads(conn):
    from app import scheduling

    conn.execute("INSERT INTO uploads (id, filename, created_at) "
                 "VALUES ('expired', 'finance.csv', '2020-01-01 00:00:00')")
    conn.commit()
    scheduling.create_job(conn, "retention_purge", "daily")
    result = scheduling.run_due_jobs(conn, llm_client=None)
    assert len(result["ran"]) == 1
    assert result["ran"][0]["status"] == "ok"
    assert result["ran"][0]["uploads_purged"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM uploads").fetchone()["n"] == 0
