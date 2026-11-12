"""Dataset freshness follows value writes, rather than metadata edits or reads."""

import sqlite3
from datetime import datetime, timezone

import pytest
from test_datasets import client as client
from test_import_honesty import LONG, _import, _upload

from app import db

OLD = "2001-01-01T00:00:00Z"


def _reset(ds):
    conn = db.get_connection()
    try:
        conn.execute("UPDATE datasets SET data_changed_at = ? WHERE id = ?", (OLD, ds))
        conn.commit()
    finally:
        conn.close()


def _changed(api, ds):
    return next(d for d in api.get("/datasets").json()["datasets"] if d["id"] == ds)["data_changed_at"]


def test_mapping_freshness_reaches_dataset_and_amount_responses(client):
    before = datetime.now(timezone.utc)
    result = _import(client, "period,metric,value\n2025-01,Revenue,100\n2025-02,Revenue,120\n")
    ds = result["dataset_id"]
    changed = result["data_changed_at"]
    assert before <= datetime.fromisoformat(changed.replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    assert _changed(client, ds) == changed
    for response in [client.get("/datasets"), client.post(f"/datasets/{ds}/activate"), client.get("/domain"),
                     client.get("/kpis"), client.get("/forecast?metric=Revenue"),
                     client.post("/scenario", json={"metric": "Revenue"})]:
        assert response.status_code == 200, response.text
        assert response.json()["data_changed_at"] == changed
    assert client.get("/facts?period=2025-01").json()[0]["data_changed_at"] == changed
    assert client.get("/facts?period=2025-Q1&granularity=quarter").json()[0]["data_changed_at"] == changed
    assert client.get("/facts?period=2025&granularity=year").json()[0]["data_changed_at"] == changed
    response = client.patch(f"/datasets/{ds}", json={"name": "Renamed", "currency": "EUR", "fiscal_year_start": 4})
    assert response.json()["data_changed_at"] == changed
    assert _changed(client, ds) == changed


def test_budget_changes_freshness_only_when_values_written(client):
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n")["dataset_id"]
    _reset(ds)
    for metric, unchanged in [("Missing", True), ("Revenue", False)]:
        up = _upload(client, f"period,metric,value\n2025-01,{metric},90\n")
        response = client.post(f"/ingest/{up}/join-budget", json={"target_dataset_id": ds, "mapping": LONG})
        assert response.status_code == 200, response.text
        assert (_changed(client, ds) == OLD) is unchanged


def test_connector_and_custom_kpi_refresh_change_freshness(client):
    from app import connectors
    from app.compute import derived

    conn = db.get_connection()
    try:
        cid = connectors.create_connector(conn, "csv_url", "Offline", {"url": "https://example.test/data"}, "Synced", None)
        result = connectors.sync_connector(conn, cid, None, fetch=lambda _: b"period,metric,value,budget\n2025-01,Revenue,100,90\n")
        assert result["status"] == "ok"
        ds = result["dataset_id"]
    finally:
        conn.close()
    assert _changed(client, ds)
    _reset(ds)
    conn = db.get_connection()
    try:
        failed = connectors.sync_connector(conn, cid, None, fetch=lambda _: b"bad source")
        assert failed["status"] == "error"
    finally:
        conn.close()
    assert _changed(client, ds) == OLD
    response = client.post("/kpis/derived", json={"name": "Twice", "formula": "[Revenue] * 2"})
    assert response.status_code == 201, response.text
    assert _changed(client, ds) != OLD
    _reset(ds)
    conn = db.get_connection()
    try:
        derived.refresh(conn, ds)
    finally:
        conn.close()
    assert _changed(client, ds) != OLD


@pytest.mark.sqlite_only
def test_legacy_backfill_once_and_empty_dataset_stays_null(tmp_path, monkeypatch):
    from app.config import settings
    from app.datasets import create_dataset

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "legacy-freshness.db"))
    legacy = sqlite3.connect(settings.db_path)
    schema = db._SQLITE_SCHEMA.replace("    data_changed_at TIMESTAMP,\n", "")
    # The legacy table really lacks the column (guards against the replace silently missing).
    assert "data_changed_at" not in schema.split("CREATE TABLE IF NOT EXISTS datasets", 1)[1].split(");", 1)[0]
    legacy.executescript(schema)
    legacy.execute("INSERT INTO datasets (name, created_at) VALUES ('Old', '2001-01-01 00:00:00')")
    legacy.commit()
    legacy.close()
    db.init_db()
    conn = db.get_connection()
    try:
        assert str(conn.execute("SELECT data_changed_at FROM datasets").fetchone()["data_changed_at"]).startswith("2001-01-01")
        empty = create_dataset(conn, "No values")
    finally:
        conn.close()
    db.init_db()
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT data_changed_at FROM datasets WHERE id = ?", (empty,)).fetchone()["data_changed_at"] is None
    finally:
        conn.close()
