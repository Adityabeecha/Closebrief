"""v5.0 Custom KPI Builder: safe formula evaluation + end-to-end derived metric
that flows through compute (and thus passes the faithfulness guard)."""

import pytest
from dbharness import use_test_db

from app.compute.formula import FormulaError, evaluate, referenced_metrics, validate


def test_evaluate_arithmetic():
    v = {"Net Revenue": 100.0, "COGS": 40.0}
    assert evaluate("([Net Revenue] - [COGS]) / [Net Revenue] * 100", v) == 60.0
    assert evaluate("[Net Revenue] + [COGS]", v) == 140.0
    assert evaluate("-[COGS]", v) == -40.0


def test_division_by_zero_is_none():
    assert evaluate("[A] / [B]", {"A": 5.0, "B": 0.0}) is None
    # Propagates through outer arithmetic rather than crashing.
    assert evaluate("[A] / [B] + 1", {"A": 5.0, "B": 0.0}) is None


def test_referenced_metrics_dedup_and_order():
    assert referenced_metrics("[A] + [B] - [A]") == ["A", "B"]


def test_validate_rejects_unsafe_and_empty():
    with pytest.raises(FormulaError):
        validate("100 + 5")                    # no metric reference
    with pytest.raises(FormulaError):
        validate("__import__('os').system('x')")  # not arithmetic-over-metrics
    with pytest.raises(FormulaError):
        evaluate("[A].__class__", {"A": 1.0})     # attribute access rejected


def test_missing_reference_raises():
    with pytest.raises(FormulaError):
        evaluate("[A] + [B]", {"A": 1.0})      # B missing


# ---------------------------------------------------------------- end-to-end
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


def test_derived_kpi_end_to_end_and_faithful(client):
    _ingest(client, "period,metric,value,budget\n"
                    "2025-02,Net Revenue,90,90\n2025-02,COGS,40,40\n"
                    "2025-03,Net Revenue,100,95\n2025-03,COGS,40,42\n")
    r = client.post("/kpis/derived", json={
        "name": "Gross Margin %", "unit": "%", "category": "Profitability",
        "formula": "([Net Revenue] - [COGS]) / [Net Revenue] * 100"})
    assert r.status_code == 201, r.text

    facts = client.get("/facts", params={"period": "2025-03"}).json()
    gm = next(f for f in facts if f["metric"] == "Gross Margin %")
    assert gm["value"] == 60.0   # (100-40)/100*100

    # The derived value lives in computed_facts, so a narrative citing it is
    # faithful — prove it with the guard directly.
    from app.generation.guard import check_faithfulness
    from app.schemas import ComputedFact, Deltas
    fact = ComputedFact(metric="Gross Margin %", category="Profitability", period="2025-03",
                        value=60.0, unit="%", prior_value=55.6,
                        deltas=Deltas(mom_pct=None, yoy_pct=None, budget_var_abs=None, budget_var_pct=None),
                        trend=None, is_anomaly=False)
    passed, _ = check_faithfulness("Gross Margin % reached 60.0% this period.", fact)
    assert passed is True


def test_derived_unit_propagated_to_metrics_row(client):
    # Regression: the derived metric's unit must reach the metrics table, else
    # generation loads unit='USD' and a %-KPI narrative fails the faithfulness guard.
    _ingest(client, "period,metric,value,budget\n2025-03,Net Revenue,100,90\n2025-03,COGS,40,42\n")
    client.post("/kpis/derived", json={
        "name": "GM %", "unit": "%", "formula": "([Net Revenue]-[COGS])/[Net Revenue]*100"})
    import app.main as main
    conn = main.get_connection()
    unit = conn.execute("SELECT unit FROM metrics WHERE name='GM %'").fetchone()["unit"]
    conn.close()
    assert unit == "%"


def test_derived_refreshes_on_recompute(client):
    # Regression: new base data + /compute must re-materialize the derived metric.
    _ingest(client, "period,metric,value,budget\n2025-02,Rev,90,90\n2025-02,COGS,40,40\n")
    client.post("/kpis/derived", json={"name": "GM", "formula": "[Rev]-[COGS]"})
    import app.main as main
    conn = main.get_connection()
    ds = conn.execute("SELECT id FROM datasets ORDER BY id DESC LIMIT 1").fetchone()["id"]
    for m, v in (("Rev", 100), ("COGS", 45)):
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id=? AND name=?", (ds, m)).fetchone()["id"]
        conn.execute("INSERT INTO metric_values (metric_id, period, value) VALUES (?, '2025-03', ?)", (mid, v))
    conn.commit()
    conn.close()
    client.post("/compute")
    facts = client.get("/facts", params={"period": "2025-03"}).json()
    gm = next(f for f in facts if f["metric"] == "GM")
    assert gm["value"] == 55.0   # 100 - 45, freshly materialized for the new period


def test_derived_bad_formula_422(client):
    _ingest(client, "period,metric,value,budget\n2025-03,Rev,100,90\n")
    assert client.post("/kpis/derived", json={
        "name": "X", "formula": "[Nonexistent] * 2"}).status_code == 422
    assert client.post("/kpis/derived", json={"name": "Y", "formula": ""}).status_code == 422
