"""v5.0 Cross-Domain Insights: correlate metrics across different datasets."""

import pytest

from app.compute.cross_domain import _add_months, cross_domain_correlations


def test_add_months_rolls_year():
    assert _add_months("2025-11", 3) == "2026-02"
    assert _add_months("2025-03", 0) == "2025-03"


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


def _conn():
    from app.db import get_connection
    return get_connection()


def _dataset(conn, name, domain, metric, values):
    from app.datasets import create_dataset, get_or_create_metric
    ds = create_dataset(conn, name, activate=False)
    conn.execute("UPDATE datasets SET domain = ? WHERE id = ?", (domain, ds))
    mid = get_or_create_metric(conn, ds, metric)
    for period, v in values.items():
        conn.execute("INSERT INTO metric_values (metric_id, period, value) VALUES (?, ?, ?)",
                     (mid, period, v))
    conn.commit()
    return ds


def test_detects_cross_dataset_correlation(client):
    conn = _conn()
    try:
        periods = [f"2025-{m:02d}" for m in range(1, 9)]   # 8 months
        spend = {p: 100 + 10 * i for i, p in enumerate(periods)}
        revenue = {p: 10 * spend[p] for p in periods}       # perfectly correlated
        _dataset(conn, "Marketing", "marketing", "Marketing Spend", spend)
        _dataset(conn, "Finance", "fpa", "Net Revenue", revenue)

        from app.datasets import list_datasets
        pairs = cross_domain_correlations(conn, [d["id"] for d in list_datasets(conn)])
        assert pairs, "expected a cross-dataset correlation"
        top = pairs[0]
        metrics = {top["metric_a"], top["metric_b"]}
        assert metrics == {"Marketing Spend", "Net Revenue"}
        assert top["direction"] == "positive" and abs(top["r"]) >= 0.7
        assert top["dataset_a"] != top["dataset_b"]   # cross-dataset
    finally:
        conn.close()


def test_same_dataset_pairs_excluded(client):
    conn = _conn()
    try:
        periods = [f"2025-{m:02d}" for m in range(1, 9)]
        # Two correlated metrics in the SAME dataset must NOT be reported here.
        from app.datasets import create_dataset, get_or_create_metric
        ds = create_dataset(conn, "One", activate=False)
        for name in ("A", "B"):
            mid = get_or_create_metric(conn, ds, name)
            for i, p in enumerate(periods):
                conn.execute("INSERT INTO metric_values (metric_id, period, value) VALUES (?,?,?)",
                             (mid, p, 100 + 10 * i))
        conn.commit()
        assert cross_domain_correlations(conn, [ds]) == []
    finally:
        conn.close()


def test_cross_domain_endpoint(client):
    assert client.get("/insights/cross-domain").json() == []   # <2 datasets → empty
