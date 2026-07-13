"""Scheduling backbone (v3.1): cadence math, job CRUD, digest history, the
secured cron tick, and an end-to-end anomaly-scan run (no LLM, no network)."""

from datetime import datetime, timezone

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


# ---------------------------------------------------------------- cadence math
def test_advance_daily_weekly():
    from app.scheduling import advance
    now = datetime(2025, 3, 10, 9, 0, tzinfo=timezone.utc)
    assert advance(now, "daily").day == 11
    assert advance(now, "weekly").day == 17


def test_advance_monthly_clamps_month_end():
    from app.scheduling import advance
    jan31 = datetime(2025, 1, 31, 12, 0, tzinfo=timezone.utc)
    nxt = advance(jan31, "monthly")
    assert (nxt.year, nxt.month, nxt.day) == (2025, 2, 28)   # clamped
    dec = datetime(2025, 12, 15, tzinfo=timezone.utc)
    assert (advance(dec, "monthly").year, advance(dec, "monthly").month) == (2026, 1)


def test_advance_rejects_unknown_cadence():
    from app.scheduling import advance
    with pytest.raises(ValueError):
        advance(datetime.now(timezone.utc), "hourly")


# ------------------------------------------------------------------- job CRUD
def test_job_crud_roundtrip(client):
    from app import scheduling
    conn = _conn()
    try:
        jid = scheduling.create_job(conn, "digest", "weekly", config={"top_n": 3})
        jobs = scheduling.list_jobs(conn)
        assert len(jobs) == 1 and jobs[0]["kind"] == "digest"
        assert jobs[0]["cadence"] == "weekly" and jobs[0]["config"]["top_n"] == 3
        assert jobs[0]["enabled"] is True and jobs[0]["next_run_at"] is not None
        scheduling.set_job_enabled(conn, jid, False)
        assert scheduling.list_jobs(conn)[0]["enabled"] is False
        assert scheduling.delete_job(conn, jid) is True
        assert scheduling.list_jobs(conn) == []
        assert scheduling.delete_job(conn, jid) is False
    finally:
        conn.close()


def test_create_job_validates(client):
    from app import scheduling
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            scheduling.create_job(conn, "bogus", "daily")
        with pytest.raises(ValueError):
            scheduling.create_job(conn, "digest", "hourly")
    finally:
        conn.close()


# -------------------------------------------------------------- digest history
def test_digest_history_record_and_list(client):
    from app import scheduling
    conn = _conn()
    try:
        scheduling.record_digest_run(conn, 1, "2025-03", 5,
                                     [{"metric": "Net Revenue", "headline": "up"}], 0.01, "manual")
        scheduling.record_digest_run(conn, 1, "2025-04", 5, [], 0.02, "scheduled")
        runs = scheduling.list_digest_runs(conn, dataset_id=1)
        assert [r["period"] for r in runs] == ["2025-04", "2025-03"]   # newest first
        assert runs[1]["items"][0]["metric"] == "Net Revenue"
        assert runs[0]["trigger"] == "scheduled"
    finally:
        conn.close()


# --------------------------------------------------- run_due_jobs (no LLM path)
def _ingest(client, csv_text):
    r = client.post("/ingest/upload", files={"file": ("f.csv", csv_text, "text/csv")})
    assert r.status_code == 200, r.text
    uid = r.json()["upload_id"]
    r = client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


def test_anomaly_scan_tick_runs_without_llm(client):
    from app import scheduling
    # A metric with a flat history and one large spike so the last period is anomalous.
    rows = ["period,metric,value,budget"]
    for i in range(1, 12):
        rows.append(f"2025-{i:02d},Signups,100,100")
    rows.append("2025-12,Signups,1000,110")   # spike
    ds = _ingest(client, "\n".join(rows))

    conn = _conn()
    try:
        scheduling.create_job(conn, "anomaly_scan", "daily", dataset_id=ds)
        result = scheduling.run_due_jobs(conn, llm_client=None)
        assert len(result["ran"]) == 1
        job_result = result["ran"][0]
        assert job_result["kind"] == "anomaly_scan"
        assert job_result["period"] == "2025-12"
        # No enabled channels configured -> delivered to none, but the scan ran.
        assert "anomalies" in job_result
        # After running, next_run_at advanced so a second tick does nothing.
        again = scheduling.run_due_jobs(conn, llm_client=None)
        assert again["ran"] == []
    finally:
        conn.close()


# ------------------------------------------------------------- the cron endpoint
def test_run_records_status_and_log(client):
    from app import scheduling
    rows = ["period,metric,value,budget"] + [f"2025-{i:02d},Signups,100,100" for i in range(1, 12)]
    rows.append("2025-12,Signups,1000,110")
    ds = _ingest(client, "\n".join(rows))
    conn = _conn()
    try:
        scheduling.create_job(conn, "anomaly_scan", "daily", dataset_id=ds)
        scheduling.run_due_jobs(conn, llm_client=None)
        job = scheduling.list_jobs(conn)[0]
        assert job["last_status"] == "ok" and job["fail_count"] == 0
        n = conn.execute("SELECT COUNT(*) AS n FROM scheduler_runs WHERE status='ok'").fetchone()["n"]
        assert n == 1
    finally:
        conn.close()


class _BoomLLM:
    def generate_narrative(self, system, prompt):
        raise RuntimeError("llm down")


def test_failure_increments_fail_count(client):
    from app import scheduling
    ds = _ingest(client, "period,metric,value,budget\n2025-01,Rev,100,100\n2025-02,Rev,200,100\n")
    conn = _conn()
    try:
        scheduling.create_job(conn, "digest", "daily", dataset_id=ds)
        scheduling.run_due_jobs(conn, llm_client=_BoomLLM())
        job = scheduling.list_jobs(conn)[0]
        assert job["last_status"] == "error" and job["fail_count"] == 1
        assert job["last_error"]
        errs = conn.execute("SELECT COUNT(*) AS n FROM scheduler_runs WHERE status='error'").fetchone()["n"]
        assert errs == 1
    finally:
        conn.close()


def test_tick_endpoint_disabled_without_token(client):
    r = client.post("/internal/scheduler/tick")
    assert r.status_code == 503


def test_tick_endpoint_requires_valid_token(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "scheduler_token", "sekret")
    assert client.post("/internal/scheduler/tick").status_code == 401
    assert client.post("/internal/scheduler/tick",
                       headers={"X-Scheduler-Token": "wrong"}).status_code == 401
    r = client.post("/internal/scheduler/tick", headers={"X-Scheduler-Token": "sekret"})
    assert r.status_code == 200
    assert "ran" in r.json()


def test_schedules_crud_endpoints(client):
    r = client.post("/schedules", json={"kind": "digest", "cadence": "weekly", "top_n": 4})
    assert r.status_code == 201, r.text
    jid = r.json()["id"]
    assert any(j["id"] == jid for j in client.get("/schedules").json())
    assert client.post("/schedules", json={"kind": "x", "cadence": "daily"}).status_code == 422
    assert client.patch(f"/schedules/{jid}", json={"enabled": False}).status_code == 200
    assert client.delete(f"/schedules/{jid}").status_code == 204
    assert client.delete(f"/schedules/{jid}").status_code == 404
