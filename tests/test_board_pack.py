"""Board pack (v5.1): the pure HTML builder + the /board-pack endpoint that
assembles a period's KPIs into one self-contained, print-ready document."""

import pytest
from dbharness import use_test_db

from app.board_pack import build_board_pack_html


def _fact(metric, value, unit="USD", mom=5.0, var=10.0, anomaly=False, narrative="Up on strong demand.",
          trend=None, category="Revenue"):
    return {
        "metric": metric, "category": category, "period": "2025-03", "value": value,
        "unit": unit, "direction_good": "up", "has_data": True, "is_anomaly": anomaly,
        "narrative": narrative,
        "deltas": {"mom_pct": mom, "yoy_pct": None, "budget_var_abs": var, "budget_var_pct": var},
        # /facts delivers trend oldest -> newest.
        "chart_data": {"trend": trend or [
            {"period": "2025-01", "value": value - 12, "budget": value - 10},
            {"period": "2025-02", "value": value - 6, "budget": value - 5},
            {"period": "2025-03", "value": value, "budget": value - 2},
        ]},
    }


def test_build_is_self_contained_and_renders_kpis():
    facts = [_fact("Net Revenue", 172.0), _fact("COGS", 64.0, category="Cost")]
    doc = build_board_pack_html(facts, "2025-03", {"dataset_name": "Q1 Actuals"})
    assert doc.startswith("<!doctype html>")
    # Self-contained: no external assets (would break in email / offline).
    assert "src=" not in doc and "http://" not in doc and "https://" not in doc
    assert "Net Revenue" in doc and "COGS" in doc
    assert "Q1 Actuals" in doc and "2025-03" in doc
    assert "Up on strong demand." in doc
    assert "<svg" in doc            # inline sparkline
    assert "KPIs reported" in doc


def test_anomaly_and_missing_narrative_render():
    facts = [_fact("Churn", 4.2, unit="%", anomaly=True, narrative=None)]
    doc = build_board_pack_html(facts, "2025-03", {})
    assert "Anomaly" in doc
    assert "No narrative generated" in doc
    assert "4.2%" in doc            # percent formatting


def test_html_escaped():
    facts = [_fact("<script>x</script>", 10.0, narrative="a & b <tag>")]
    doc = build_board_pack_html(facts, "2025-03", {})
    assert "<script>x</script>" not in doc
    assert "&lt;script&gt;" in doc


def test_empty_period_has_no_kpi_rows():
    doc = build_board_pack_html([], "2025-03", {"dataset_name": "Empty"})
    assert "No KPIs with data" in doc


# ----------------------------------------------------------------- endpoint
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


def _ingest(client, csv):
    uid = client.post("/ingest/upload", files={"file": ("f.csv", csv, "text/csv")}).json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})


def test_board_pack_endpoint_returns_html(client):
    _ingest(client, "period,metric,value,budget\n"
                    "2025-01,Net Revenue,100,98\n2025-01,COGS,40,41\n"
                    "2025-02,Net Revenue,110,100\n2025-02,COGS,42,42\n"
                    "2025-03,Net Revenue,124,112\n2025-03,COGS,45,44\n")
    r = client.get("/board-pack?period=2025-03")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "Board Pack" in body and "Net Revenue" in body and "2025-03" in body
    assert "<svg" in body


def test_board_pack_requires_period(client):
    assert client.get("/board-pack").status_code == 422
