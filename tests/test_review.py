"""v5.0 Collaborative Review: assign, approve, and version history with diffs."""

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
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


def _seed_report(client):
    csv = "period,metric,value,budget\n2025-03,Rev,100,90\n"
    uid = client.post("/ingest/upload", files={"file": ("f.csv", csv, "text/csv")}).json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})
    import app.main as main
    conn = main.get_connection()
    mid = conn.execute("SELECT id FROM metrics WHERE name='Rev'").fetchone()["id"]
    conn.execute("INSERT INTO generated_reports (metric_id, period, narrative, confidence, faithfulness) "
                 "VALUES (?, '2025-03', 'Revenue rose 11% on volume.', 'High', 'passed')", (mid,))
    rid = conn.execute("SELECT MAX(id) AS id FROM generated_reports").fetchone()["id"]
    conn.commit()
    conn.close()
    return rid


def test_assign_and_review_status(client):
    rid = _seed_report(client)
    r = client.post(f"/reports/{rid}/assign", json={"user_id": "rev1", "email": "cfo@co.com"})
    assert r.status_code == 200 and r.json()["review_status"] == "pending"
    r = client.post(f"/reports/{rid}/review", json={"status": "approved", "note": "looks good"})
    assert r.status_code == 200 and r.json()["review_status"] == "approved"
    # Invalid status rejected.
    assert client.post(f"/reports/{rid}/review", json={"status": "maybe"}).status_code == 422
    assert client.post("/reports/99999/assign", json={}).status_code == 404


def test_version_history_and_diffs(client):
    rid = _seed_report(client)
    # Two edits → v1 (original) + v2 + v3.
    client.post("/feedback", json={"report_id": rid, "action": "edited",
                                   "edited_text": "Revenue rose 11% on pricing."})
    client.post("/feedback", json={"report_id": rid, "action": "edited",
                                   "edited_text": "Revenue rose 11% on pricing and volume."})
    versions = client.get(f"/reports/{rid}/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[0]["narrative"] == "Revenue rose 11% on volume."   # original captured
    assert versions[2]["narrative"].endswith("pricing and volume.")
    # A diff exists between v1 and v2.
    assert any(line.startswith("+") for line in versions[1]["diff"])
