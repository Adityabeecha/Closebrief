"""Concrete regressions investigated from the TypeSafe risk classifications."""

import io
import sqlite3
from datetime import datetime
from types import SimpleNamespace
from urllib.request import Request

import numpy as np
import pytest


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from app import db
    from app.config import settings
    from app.datasets import set_demo_scope, set_workspace_scope
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "regression.db"))
    set_demo_scope(False)
    set_workspace_scope(None)
    db.init_db()
    connection = db.get_connection()
    yield connection
    connection.close()
    set_demo_scope(False)
    set_workspace_scope(None)


def metric(conn, name="Revenue", dataset=None):
    from app.datasets import create_dataset, get_or_create_metric
    dataset = dataset or create_dataset(conn, "Dataset")
    return dataset, get_or_create_metric(conn, dataset, name)


def test_context_get_update_delete_cannot_cross_workspace(conn, tmp_path):
    from app.context.store import ContextStore
    from app.context.vector_store import FaissVectorStore
    from app.datasets import set_workspace_scope
    from app.schemas import ContextDocIn
    embedder = SimpleNamespace(embed=lambda texts: np.ones((len(texts), 8), dtype=np.float32))
    store = ContextStore(conn, embedder, FaissVectorStore(8, str(tmp_path / "index")))
    doc = ContextDocIn(type="event_note", title="Private", body="Workspace document")
    set_workspace_scope(1)
    saved = store.add(doc)
    set_workspace_scope(2)
    assert store.get(saved.id) is None
    assert store.update(saved.id, doc) is None
    assert store.delete(saved.id) is False
    set_workspace_scope(1)
    assert store.get(saved.id).title == "Private"


def test_uploads_and_templates_cannot_cross_workspace(conn, tmp_path, monkeypatch):
    from app.datasets import set_workspace_scope
    from app.ingestion import templates, upload
    monkeypatch.setattr(upload, "UPLOAD_DIR", tmp_path / "uploads")
    set_workspace_scope(1)
    result = upload.create_upload(conn, b"A,B\n1,2\n", "data.csv")
    template = templates.save_template(conn, upload.column_signature(["A", "B"]), {"metric_col": "A"})
    set_workspace_scope(2)
    with pytest.raises(upload.UploadError):
        upload.load_upload_frame(conn, result["upload_id"])
    assert templates.get_template(conn, template) is None
    assert templates.match_template(conn, upload.column_signature(["A", "B"])) is None
    second = templates.save_template(conn, upload.column_signature(["A", "B"]), {"metric_col": "B"})
    assert second != template
    set_workspace_scope(1)
    assert templates.get_template(conn, template)["mapping"]["metric_col"] == "A"


@pytest.mark.parametrize("raw,name", [(b"garbage", "broken.xlsx"), (b'"unterminated', "broken.csv"), (b"data", "bad.exe")])
def test_bad_upload_returns_validation_error_and_leaves_no_file(conn, tmp_path, monkeypatch, raw, name):
    from app.ingestion import upload
    folder = tmp_path / "uploads"
    monkeypatch.setattr(upload, "UPLOAD_DIR", folder)
    with pytest.raises(upload.UploadError):
        upload.create_upload(conn, raw, name)
    assert not folder.exists() or not list(folder.iterdir())
    assert conn.execute("SELECT count(*) FROM uploads").fetchone()[0] == 0


@pytest.mark.parametrize("row", ["not-a-month,Revenue,1,2", "2025-13,Revenue,1,2", "2025-01,Revenue,inf,2",
                                 "2025-01,Revenue,1,inf", "2025-01,,1,2"])
def test_canonical_ingest_rejects_invalid_period_metric_or_nonfinite_values(row):
    from app.ingestion.ingest import IngestValidationError, parse_csv
    with pytest.raises(IngestValidationError):
        parse_csv(("period,metric,value,budget\n" + row + "\n").encode())


def test_trend_streak_requires_calendar_continuity_and_current_period(conn):
    from app.compute.correlations import detect_consecutive_trends
    _, mid = metric(conn)
    for period in ("2025-01", "2025-02", "2025-04"):
        conn.execute("INSERT INTO computed_facts(metric_id,period,value,mom_pct) VALUES (?,?,100,5)", (mid, period))
    conn.commit()
    assert detect_consecutive_trends(conn, mid, "2025-04") is None
    assert detect_consecutive_trends(conn, mid, "2025-05") is None


def test_derived_chain_refreshes_and_removes_now_undefined_values(conn):
    from app.compute.derived import create_derived_metric, refresh
    ds, a = metric(conn, "A")
    _, b = metric(conn, "B", ds)
    for mid, value in ((a, 10), (b, 2)):
        conn.execute("INSERT INTO metric_values(metric_id,period,value) VALUES (?,'2025-01',?)", (mid, value))
    conn.commit()
    create_derived_metric(conn, ds, "Ratio", "[A]/[B]")
    create_derived_metric(conn, ds, "Twice", "[Ratio]*2")
    refresh(conn, ds)
    rows = conn.execute("SELECT m.name,cf.value FROM computed_facts cf JOIN metrics m ON m.id=cf.metric_id").fetchall()
    assert {r["name"]: r["value"] for r in rows}["Twice"] == 10
    conn.execute("UPDATE metric_values SET value=0 WHERE metric_id=?", (b,))
    conn.commit()
    refresh(conn, ds)
    assert conn.execute("SELECT count(*) FROM computed_facts cf JOIN metrics m ON m.id=cf.metric_id "
                        "WHERE m.name IN ('Ratio','Twice')").fetchone()[0] == 0


def test_derived_name_cannot_overwrite_base_metric(conn):
    from app.compute.derived import create_derived_metric
    from app.compute.formula import FormulaError
    ds, _ = metric(conn, "Revenue")
    with pytest.raises(FormulaError):
        create_derived_metric(conn, ds, "Revenue", "[Revenue]*2")


def test_derived_undefined_month_is_not_treated_as_zero(conn):
    from app.compute.derived import create_derived_metric, refresh
    ds, a = metric(conn, "A")
    _, b = metric(conn, "B", ds)
    for period, divisor in (("2025-01", 2), ("2025-02", 0)):
        for mid, value in ((a, 10), (b, divisor)):
            conn.execute("INSERT INTO metric_values(metric_id,period,value) VALUES (?,?,?)", (mid, period, value))
    conn.commit()
    create_derived_metric(conn, ds, "Ratio", "[A]/[B]")
    create_derived_metric(conn, ds, "Combined", "[Ratio]+[A]")
    refresh(conn, ds)
    rows = conn.execute("SELECT cf.period,cf.value FROM computed_facts cf JOIN metrics m ON m.id=cf.metric_id "
                        "WHERE m.name='Combined'").fetchall()
    assert [(r["period"], r["value"]) for r in rows] == [("2025-01", 15)]


def test_eval_vector_store_does_not_modify_production_table(monkeypatch):
    from app.context import pg_vector_store as module
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    for table in ("context_vectors", "eval_context_vectors"):
        raw.execute(f"CREATE TABLE {table}(context_id INTEGER PRIMARY KEY, embedding TEXT, dim INTEGER)")
    adapter = SimpleNamespace(execute=lambda sql, args: raw.execute(sql.replace("::vector", ""), args),
                              commit=raw.commit, close=lambda: None)
    monkeypatch.setattr(module, "get_connection", lambda: adapter)
    store = module.PgVectorStore(2, table="eval_context_vectors")
    store.add([1], np.ones((1, 2)))
    assert raw.execute("SELECT count(*) FROM context_vectors").fetchone()[0] == 0
    assert raw.execute("SELECT count(*) FROM eval_context_vectors").fetchone()[0] == 1
    assert store.size == 1
    store.remove([1])
    assert store.size == 0
    raw.close()


def test_compute_all_datasets_does_not_mix_equal_metric_names(conn):
    from app.compute.kpis import compute_and_store
    _, one = metric(conn)
    _, two = metric(conn)
    for mid, value in ((one, 100), (two, 500)):
        conn.execute("INSERT INTO metric_values(metric_id,period,value) VALUES (?,'2025-01',?)", (mid, value))
    conn.commit()
    compute_and_store(conn)
    rows = conn.execute("SELECT metric_id,value FROM computed_facts ORDER BY metric_id").fetchall()
    assert [(r["metric_id"], r["value"]) for r in rows] == [(one, 100), (two, 500)]


def test_audit_verifies_postgres_timestamp_representation(conn):
    from app import audit
    audit.record(conn, "create", "report", 1)
    original = dict(conn.execute("SELECT * FROM audit_log").fetchone())
    original["created_at"] = datetime.fromisoformat(original["created_at"])
    pg = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: [original]))
    assert audit.verify(pg, None)["ok"] is True


def test_audit_detects_tampered_previous_hash(conn):
    from app import audit
    audit.record(conn, "create", "report", 1)
    audit.record(conn, "create", "report", 2)
    conn.execute("UPDATE audit_log SET prev_hash='tampered' WHERE id=2")
    conn.commit()
    assert audit.verify(conn, None)["ok"] is False


def test_postgres_close_never_commits_uncommitted_business_changes(monkeypatch):
    from app.config import settings
    from app.db import PostgresConnection
    monkeypatch.setattr(settings, "rls_enabled", True)
    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE TABLE test(value INT)")
    raw.execute("INSERT INTO test VALUES(1)")
    # Support PG's scope statement on a real SQLite transaction to reproduce the commit bug.
    class Adapter:
        def execute(self, *args):
            return raw.execute("SELECT 1")
        def commit(self):
            raw.commit()
        def rollback(self):
            raw.rollback()
    pool = SimpleNamespace(putconn=lambda conn: None)
    PostgresConnection(Adapter(), pool).close()
    assert raw.execute("SELECT count(*) FROM test").fetchone()[0] == 0
    raw.close()


def test_connector_redirect_to_loopback_is_rejected(monkeypatch):
    import socket
    import urllib.request

    from app import connectors
    from app.notifications import channels
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1" if host == "localhost" else "93.184.216.34", 443))])
    # Intercept the opener after construction, then exercise its real redirect handler.
    original = urllib.request.build_opener
    def opener(*handlers):
        built = original(*handlers)
        redirect = next(h for h in built.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
        redirect.redirect_request(Request("https://example.com/data"), io.BytesIO(), 302, "Found", {}, "http://localhost/private")
        pytest.fail("Redirect to a private host was accepted")
    monkeypatch.setattr(urllib.request, "build_opener", opener)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("Unguarded default opener used"))
    with pytest.raises(channels.NotificationError):
        connectors.fetch_bytes("https://example.com/data")


def test_public_download_redirect_drops_credentials(monkeypatch):
    import socket

    from app.notifications.channels import _PublicRedirectHandler
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    request = Request("https://example.com/export", headers={"Authorization": "test-credential", "Cookie": "test-cookie"})
    redirected = _PublicRedirectHandler().redirect_request(
        request, io.BytesIO(), 302, "Found", {}, "https://download.example.org/file")
    assert redirected.full_url == "https://download.example.org/file"
    assert not redirected.has_header("Authorization")
    assert not redirected.has_header("Cookie")


def test_oversized_secondary_sheet_is_rejected_not_truncated(conn, tmp_path, monkeypatch):
    import pandas as pd

    from app.ingestion import upload
    monkeypatch.setattr(upload, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(upload, "MAX_ROWS", 2)
    workbook = io.BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame({"A": [1]}).to_excel(writer, sheet_name="Small", index=False)
        pd.DataFrame({"A": [1, 2, 3, 4]}).to_excel(writer, sheet_name="Large", index=False)
    saved = upload.create_upload(conn, workbook.getvalue(), "data.xlsx")
    with pytest.raises(upload.UploadError):
        upload.load_upload_frame(conn, saved["upload_id"], "Large")
