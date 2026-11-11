"""/facts batches each card's chart and streak data (audit P1: ~7 queries per
card made the dashboard take ~9 s on a remote Postgres). The batched path must
return exactly what the per-metric helpers return."""

from pathlib import Path

import pytest
from dbharness import use_test_db

import app.routers.facts as route_facts

SAMPLE = Path(__file__).parents[1] / "data" / "sample_fpa.csv"


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
        r = c.post("/ingest", files={"file": ("sample_fpa.csv", SAMPLE.read_bytes(), "text/csv")})
        assert r.status_code == 200, r.text
        from app.compute.kpis import compute_and_store

        conn = main.get_connection()
        try:
            assert not compute_and_store(conn, route_facts.active_dataset_id(conn)).empty
        finally:
            conn.close()
        yield c


def test_batched_cards_match_the_per_metric_helpers(client):
    import app.main as main
    from app.compute.correlations import detect_consecutive_trends

    periods = client.get("/periods").json()
    streaks_seen = 0
    for period in (periods[0], periods[len(periods) // 2], periods[-1]):
        facts = client.get(f"/facts?period={period}").json()
        conn = main.get_connection()
        try:
            for f in facts:
                if not f["has_data"]:
                    continue
                mid = conn.execute(
                    "SELECT id FROM metrics WHERE name = ? AND dataset_id = ?",
                    (f["metric"], route_facts.active_dataset_id(conn)),
                ).fetchone()["id"]
                assert f["chart_data"] == route_facts._chart_data(conn, mid, f["metric"], period)
                assert f["trend_streak"] == detect_consecutive_trends(conn, mid, period)
                streaks_seen += f["trend_streak"] is not None
        finally:
            conn.close()
    # The sample has real streaks, so the comparison covered non-empty ones too.
    assert streaks_seen > 0


def test_facts_query_count_no_longer_grows_per_card(client, monkeypatch):
    import app.main as main

    calls = {"n": 0}
    real = main.get_connection

    class Counting:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, *a, **k):
            calls["n"] += 1
            return self._conn.execute(*a, **k)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(route_facts, "get_connection", lambda *a, **k: Counting(real(*a, **k)))
    period = client.get("/periods").json()[-1]
    facts = client.get(f"/facts?period={period}").json()
    cards = sum(f["has_data"] for f in facts)
    assert cards >= 5
    # Fixed overhead plus three batched queries, not ~7 per card.
    assert calls["n"] <= 12, calls["n"]
