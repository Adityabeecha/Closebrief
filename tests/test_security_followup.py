"""Behavioral regressions from the second local TypeSafe investigation."""

from types import SimpleNamespace

import pytest
from test_review_regressions import conn as _isolated_conn

import app.routers.datasets as route_datasets
import app.routers.generate as route_generate

conn = _isolated_conn


def test_failed_rls_setup_does_not_return_usable_connection(monkeypatch):
    from app import db
    from app.config import settings
    returned = []
    closed = []
    def fail(*args):
        raise RuntimeError("scope setup failed")
    raw = SimpleNamespace(execute=fail, rollback=lambda: None, close=lambda: closed.append(True))
    pool = SimpleNamespace(getconn=lambda: raw, putconn=lambda connection: returned.append(connection))
    monkeypatch.setattr(settings, "database_url", "offline-test")
    monkeypatch.setattr(settings, "rls_enabled", True)
    monkeypatch.setattr(db, "_get_pg_pool", lambda: pool)
    with pytest.raises(RuntimeError):
        db.get_connection()
    assert closed and returned == [raw]


def test_pool_cleanup_failure_closes_and_returns_connection(monkeypatch):
    from app.config import settings
    from app.db import PostgresConnection
    monkeypatch.setattr(settings, "rls_enabled", False)
    returned, closed = [], []
    def fail():
        raise RuntimeError("broken connection")
    raw = SimpleNamespace(rollback=fail, close=lambda: closed.append(True))
    # psycopg_pool.putconn accepts a connection, not a close=True parameter.
    pool = SimpleNamespace(putconn=lambda connection: returned.append(connection))
    PostgresConnection(raw, pool).close()
    assert closed and returned == [raw]


def test_audit_malformed_summary_is_reported_as_tampering(conn):
    from app import audit
    audit.record(conn, "create", "report", 1)
    conn.execute("UPDATE audit_log SET summary='not-json'")
    conn.commit()
    assert audit.verify(conn, None) == {"ok": False, "checked": 1, "first_bad_id": 1}


def test_dataset_rename_cannot_cross_workspaces(conn):
    from fastapi import HTTPException

    from app import datasets
    datasets.set_workspace_scope(1)
    ds = datasets.create_dataset(conn, "Original")
    datasets.set_workspace_scope(2)
    with pytest.raises(HTTPException) as error:
        route_datasets.rename_dataset(ds, {"name": "Attacker"})
    assert error.value.status_code == 404
    assert conn.execute("SELECT name FROM datasets WHERE id=?", (ds,)).fetchone()[0] == "Original"


def test_empty_workspace_digest_history_does_not_expose_other_tenants(conn):
    from app import datasets, scheduling
    datasets.set_workspace_scope(1)
    ds = datasets.create_dataset(conn, "Private")
    scheduling.record_digest_run(conn, ds, "2025-01", 1, [{"headline": "private"}], 0, "manual")
    datasets.set_workspace_scope(2)
    assert route_generate.digest_history() == []
