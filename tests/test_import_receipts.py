"""Saved operation receipts must reconcile only the figures that operation wrote."""

import json

from test_auth import _bearer
from test_auth import make_client as make_client
from test_datasets import client as client
from test_import_honesty import LONG, _import, _upload

from app.db import get_connection


def test_mapping_receipt_survives_and_totals_match_stored_figures(client):
    mapping = {**LONG, "budget_col": "budget", "id_col": "id", "dimension_cols": ["region"]}
    result = _import(client, "period,metric,value,budget,id,region\n"
                     "2025-01,Revenue,100,90,A,East\n2025-01,Revenue,100,90,A,East\n"
                     "2025-01,Revenue,200,180,A,West\n2025-02,Revenue,400,,A,East\n"
                     "2025-01,Cost,50,,B,East\n", mapping)
    response = client.get(f"/datasets/{result['dataset_id']}/receipts")
    assert response.status_code == 200, response.text
    receipt = response.json()[0]
    assert receipt["kind"] == "import" and receipt["filename"] == "honesty.csv"
    assert all(receipt["mapping"][k] == v for k, v in mapping.items())
    assert receipt["created_at"] and receipt["id"]
    assert receipt["report"] == result["import_report"]
    assert receipt["kpi_totals"] == [
        {"metric": "Cost", "periods": 1, "value_total": 50, "budget_total": None, "rows": 1},
        {"metric": "Revenue", "periods": 2, "value_total": 700, "budget_total": 270, "rows": 3},
    ]
    conn = get_connection()
    try:
        stored = conn.execute("SELECT dataset_id, mapping, report FROM import_receipts WHERE id = ?", (receipt["id"],)).fetchone()
        assert stored["dataset_id"] == result["dataset_id"]
        assert json.loads(stored["report"]) == receipt["report"]
    finally:
        conn.close()
    assert client.delete(f"/datasets/{result['dataset_id']}").status_code == 204
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM import_receipts").fetchone()["n"] == 0
    finally:
        conn.close()


def test_budget_receipt_uses_matched_names_and_only_affected_periods(client):
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n2025-02,Revenue,200\n2025-01,Cost,40\n")["dataset_id"]
    up = _upload(client, "period,metric,value\n2025-01,revenue,90\n2025-03,Revenue,10\n2025-01,Unknown,12\n")
    response = client.post(f"/ingest/{up}/join-budget", json={"target_dataset_id": ds, "mapping": LONG})
    assert response.status_code == 200, response.text
    receipts = client.get(f"/datasets/{ds}/receipts").json()
    assert [r["kind"] for r in receipts] == ["budget_join", "import"]
    assert receipts[0]["report"] == {k: v for k, v in response.json().items() if k != "dataset_id"}
    assert receipts[0]["kpi_totals"] == [
        {"metric": "Revenue", "periods": 1, "value_total": 100, "budget_total": 90, "rows": 1}]
    # Import receipt stays a snapshot after the budget operation.
    assert receipts[1]["kpi_totals"][1]["budget_total"] is None


def test_connector_receipts_capture_only_this_sync_and_survive_overwrites(client):
    from app import connectors

    conn = get_connection()
    try:
        cid = connectors.create_connector(conn, "csv_url", "Nightly", {"url": "https://example.test/offline"}, "Synced", None)
        first = connectors.sync_connector(conn, cid, None, fetch=lambda _: b"period,metric,value,budget\n2025-01,Revenue,100,90\n")
        second = connectors.sync_connector(conn, cid, None, fetch=lambda _: b"period,metric,value,budget\n2025-02,Revenue,200,180\n")
        assert first["status"] == second["status"] == "ok"
        ds = second["dataset_id"]
    finally:
        conn.close()
    receipts = client.get(f"/datasets/{ds}/receipts").json()
    assert len(receipts) == 2
    assert all(r["kind"] == "connector_sync" and r["filename"] == "Nightly" for r in receipts)
    assert receipts[0]["kpi_totals"] == [
        {"metric": "Revenue", "periods": 1, "value_total": 200, "budget_total": 180, "rows": 1}]
    assert receipts[1]["kpi_totals"][0]["value_total"] == 100


def test_receipts_are_scoped_like_datasets(make_client):
    caller = "00000000-0000-4000-8000-000000000076"
    api = make_client(auth_on=True, seed_roles={caller: ("reader@example.test", "admin")})
    from app import datasets, workspaces

    conn = get_connection()
    try:
        ws = workspaces.create_workspace(conn, "Other", "00000000-0000-4000-8000-000000000077")
        ds = datasets.create_dataset(conn, "Private", workspace_id=ws, activate=False)
    finally:
        conn.close()
    assert api.get(f"/datasets/{ds}/receipts", headers=_bearer(sub=caller, email="reader@example.test")).status_code == 404


def test_receipt_failure_does_not_fail_import_or_poison_connection(client, monkeypatch, caplog):
    from app import import_receipts

    def broken_totals(*args):
        raise RuntimeError("offline receipt failure")

    monkeypatch.setattr(import_receipts, "_kpi_totals", broken_totals)
    ds = _import(client, "period,metric,value\n2025-01,Revenue,123\n")["dataset_id"]
    assert client.get("/facts?period=2025-01").json()[0]["value"] == 123
    assert client.get(f"/datasets/{ds}/receipts").json() == []
    assert "Could not save import receipt" in caplog.text


def test_receipt_sql_failure_rolls_back_only_receipt(client, caplog):
    from app.import_receipts import record_receipt

    ds = _import(client, "period,metric,value\n2025-01,Revenue,123\n")["dataset_id"]
    conn = get_connection()
    try:
        record_receipt(conn, ds, kind="invalid", filename="bad", mapping={}, report={}, written_pairs=[])
        assert conn.execute("SELECT COUNT(*) AS n FROM metric_values").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM import_receipts").fetchone()["n"] == 1
    finally:
        conn.close()
    assert "Could not save import receipt" in caplog.text
