"""An import that stops before Finish is marked, shown as unfinished and cleaned up."""

from test_datasets import client as client  # noqa: F401
from test_import_honesty import LONG, _upload

from app.db import get_connection
from app.retention import purge_abandoned_imports

FILE = "period,metric,value\n2026-08,Revenue,100\n2026-09,Revenue,110\n"


def _datasets(api):
    body = api.get("/datasets").json()
    return body.get("datasets", body) if isinstance(body, dict) else body


def test_an_unfinished_import_is_marked_until_finish(client):  # noqa: F811
    ds = client.post(f"/ingest/{_upload(client, FILE)}/mapping", json=LONG).json()["dataset_id"]
    assert next(d for d in _datasets(client) if d["id"] == ds)["import_pending"] is True
    client.post("/kpis", json={"kpis": [{"source_metric": "Revenue", "display_name": "Revenue", "category": "Revenue",
                                         "unit": "USD", "direction_good": "up"}]})
    assert next(d for d in _datasets(client) if d["id"] == ds)["import_pending"] is False


def test_abandoned_imports_are_purged_after_two_days(client):  # noqa: F811
    ds = client.post(f"/ingest/{_upload(client, FILE)}/mapping", json=LONG).json()["dataset_id"]
    conn = get_connection()
    try:
        assert purge_abandoned_imports(conn) == 0   # too recent
        conn.execute("UPDATE datasets SET created_at = '2020-01-01 00:00:00' WHERE id = ?", (ds,))
        conn.commit()
        assert purge_abandoned_imports(conn) == 1
        assert conn.execute("SELECT 1 FROM datasets WHERE id = ?", (ds,)).fetchone() is None
    finally:
        conn.close()
