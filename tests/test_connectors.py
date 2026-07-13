"""v4.0 live data connectors: URL building, SSRF guard, CRUD, and a full sync
(fetch injected, no network) that ingests into a tenant-scoped dataset."""

import pytest
from dbharness import use_test_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main
    from app.config import settings

    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "vector_backend", "faiss")
    monkeypatch.setattr(settings, "embedding_provider", "offline")
    monkeypatch.setattr(settings, "supabase_url", "")

    from app.deps import shared_cache, shared_embedder, shared_vector_store
    shared_cache.cache_clear()
    shared_embedder.cache_clear()
    shared_vector_store.cache_clear()

    main.init_db()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def _conn():
    from app.db import get_connection
    return get_connection()


def test_build_url_google_sheets_and_csv():
    from app import connectors
    assert connectors.build_url("csv_url", {"url": "https://x.com/data.csv"}) == "https://x.com/data.csv"
    gs = connectors.build_url("google_sheets", {"sheet_id": "ABC123", "gid": "7"})
    assert gs == "https://docs.google.com/spreadsheets/d/ABC123/export?format=csv&gid=7"
    with pytest.raises(ValueError):
        connectors.build_url("csv_url", {})
    with pytest.raises(ValueError):
        connectors.build_url("google_sheets", {})


def test_fetch_blocks_ssrf_to_loopback():
    from app import connectors
    from app.notifications.channels import NotificationError
    with pytest.raises(NotificationError):
        connectors.fetch_bytes("http://127.0.0.1/secrets")


_CSV = b"period,metric,value,budget\n2025-02,Rev,100,90\n2025-03,Rev,140,110\n"


def test_sync_ingests_and_recomputes(client):
    from app import connectors
    conn = _conn()
    try:
        cid = connectors.create_connector(
            conn, "csv_url", "Nightly", {"url": "https://example.com/f.csv"}, "Synced FP&A", None)
        # Inject a fetch so no network is touched.
        res = connectors.sync_connector(conn, cid, None, fetch=lambda url: _CSV)
        assert res["status"] == "ok" and res["rows"] == 2
        ds = conn.execute("SELECT id FROM datasets WHERE name = 'Synced FP&A'").fetchone()
        assert ds is not None
        facts = conn.execute(
            "SELECT COUNT(*) AS n FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id "
            "WHERE m.dataset_id = ?", (ds["id"],)).fetchone()["n"]
        assert facts == 2   # both periods computed

        # Re-sync is idempotent (upsert by period → still 2 facts, status recorded).
        connectors.sync_connector(conn, cid, None, fetch=lambda url: _CSV)
        row = conn.execute("SELECT last_status FROM connectors WHERE id = ?", (cid,)).fetchone()
        assert row["last_status"] == "ok"
    finally:
        conn.close()


def test_sync_records_error_on_bad_source(client):
    from app import connectors
    conn = _conn()
    try:
        cid = connectors.create_connector(conn, "csv_url", "Bad", {"url": "https://x/y.csv"}, "D", None)

        def _boom(url):
            raise RuntimeError("host unreachable")

        res = connectors.sync_connector(conn, cid, None, fetch=_boom)
        assert res["status"] == "error"
        row = conn.execute("SELECT last_status, last_error FROM connectors WHERE id = ?", (cid,)).fetchone()
        assert row["last_status"] == "error" and "unreachable" in row["last_error"]
    finally:
        conn.close()


def test_connector_endpoints(client):
    r = client.post("/connectors", json={
        "kind": "google_sheets", "name": "Sheet sync",
        "config": {"sheet_id": "ABC"}, "dataset_name": "Sheet data"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    listing = client.get("/connectors").json()
    assert any(c["id"] == cid and c["kind"] == "google_sheets" for c in listing)
    # Bad kind rejected.
    assert client.post("/connectors", json={"kind": "sap", "name": "x"}).status_code == 422
    assert client.delete(f"/connectors/{cid}").status_code == 204
    assert client.delete(f"/connectors/{cid}").status_code == 404
