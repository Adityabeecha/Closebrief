"""v4.0 immutable audit trail: append + hash-chain verification + tamper detection."""

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


def test_chain_appends_and_verifies(client):
    from app import audit
    conn = _conn()
    try:
        audit.record(conn, "generate", "report", 1, actor_id="u", summary={"metric": "Rev"})
        audit.record(conn, "accepted", "report", 1, actor_id="u")
        audit.record(conn, "create", "context", 5, actor_id="u", summary={"title": "note"})
        entries = audit.list_entries(conn, None)
        assert len(entries) == 3 and entries[0]["action"] == "create"   # newest first
        assert audit.verify(conn, None)["ok"] is True
    finally:
        conn.close()


def test_tampering_breaks_the_chain(client):
    from app import audit
    conn = _conn()
    try:
        audit.record(conn, "generate", "report", 1, actor_id="u", summary={"v": 1})
        audit.record(conn, "accepted", "report", 1, actor_id="u")
        assert audit.verify(conn, None)["ok"] is True
        # Someone edits an earlier row's content directly in the DB.
        conn.execute("UPDATE audit_log SET action = 'rejected' WHERE id = 1")
        conn.commit()
        v = audit.verify(conn, None)
        assert v["ok"] is False and v["first_bad_id"] == 1
    finally:
        conn.close()


def test_deleting_a_row_breaks_the_chain(client):
    from app import audit
    conn = _conn()
    try:
        for i in range(3):
            audit.record(conn, "generate", "report", i, actor_id="u")
        conn.execute("DELETE FROM audit_log WHERE id = 2")   # remove a middle link
        conn.commit()
        assert audit.verify(conn, None)["ok"] is False
    finally:
        conn.close()


def test_feedback_writes_an_audit_entry(client):
    csv = "period,metric,value,budget\n2025-03,Rev,100,90\n"
    uid = client.post("/ingest/upload", files={"file": ("f.csv", csv, "text/csv")}).json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})
    import app.main as main
    conn = main.get_connection()
    mid = conn.execute("SELECT id FROM metrics WHERE name='Rev'").fetchone()["id"]
    conn.execute("INSERT INTO generated_reports (metric_id, period, confidence, faithfulness) "
                 "VALUES (?, '2025-03', 'High', 'passed')", (mid,))
    rid = conn.execute("SELECT MAX(id) AS id FROM generated_reports").fetchone()["id"]
    conn.commit()
    conn.close()

    assert client.post("/feedback", json={"report_id": rid, "action": "accepted"}).status_code == 201
    trail = client.get("/audit").json()
    assert any(e["action"] == "accepted" and e["entity_type"] == "report" for e in trail)
    assert client.get("/audit/verify").json()["ok"] is True
