"""v3.0 Narrative Drill-Down: sentence-level attribution + the grounding endpoint."""

import json

import pytest
from dbharness import use_test_db

from app.grounding import attribute
from app.schemas import ComputedFact, ContextSnippet, Deltas


def _fact():
    return ComputedFact(
        metric="Net Revenue", category="Revenue", period="2025-03", value=5_330_000.0,
        unit="USD", prior_value=4_180_000.0,
        deltas=Deltas(mom_pct=27.6, yoy_pct=None, budget_var_abs=600000.0, budget_var_pct=12.7),
        trend=None, is_anomaly=False,
    )


def _ctx():
    return [ContextSnippet(id="ctx_007", type="event_note", title="March 2025 pricing change",
                           body="On March 1 2025 we raised prices 15%, expecting $600K uplift.")]


def test_verified_sentence_maps_numbers_to_fact_fields():
    narr = "Net Revenue came in at $5.33M for 2025-03, up 27.6% month-over-month and 12.7% ahead of plan."
    g = attribute(narr, _fact(), _ctx())
    assert g["faithful"] is True and g["unverified"] == []
    s = g["sentences"][0]
    assert s["kind"] == "verified"
    fields = {f["field"] for f in s["facts"]}
    assert "current value" in fields and "month-over-month %" in fields and "budget variance %" in fields


def test_context_sentence_attributes_to_doc():
    narr = "Net Revenue rose. This aligns with the March 2025 pricing change."
    g = attribute(narr, _fact(), _ctx())
    ctx_sentence = [s for s in g["sentences"] if s["context_ids"]]
    assert ctx_sentence and ctx_sentence[0]["context_ids"] == ["ctx_007"]
    assert ctx_sentence[0]["kind"] == "context"


def test_invented_number_is_unverified():
    narr = "Net Revenue was $5.33M. We onboarded 47 new logos this month."
    g = attribute(narr, _fact(), _ctx())
    assert 47.0 in g["unverified"] and g["faithful"] is False
    bad = [s for s in g["sentences"] if s["kind"] == "numeric-unverified"]
    assert bad and "47" in bad[0]["text"]


def test_period_digits_not_flagged():
    # "2025-03" must not count the 2025 or 03 as invented figures.
    g = attribute("Net Revenue for 2025-03 was $5.33M.", _fact(), [])
    assert g["unverified"] == [] and g["faithful"] is True


def test_general_sentence_has_no_grounding():
    g = attribute("The team will review this closely.", _fact(), _ctx())
    assert g["sentences"][0]["kind"] == "general"
    assert g["sentences"][0]["facts"] == [] and g["sentences"][0]["context_ids"] == []


def test_spans_index_into_original_text():
    narr = "First sentence here. Second one follows."
    g = attribute(narr, _fact(), [])
    for s in g["sentences"]:
        assert narr[s["start"]:s["end"]] == s["text"]


# ---------------------------------------------------------------- endpoint
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


def test_grounding_endpoint(client):
    csv = "period,metric,value,budget\n2025-02,Net Revenue,4180000,4100000\n2025-03,Net Revenue,5330000,4730000\n"
    r = client.post("/ingest/upload", files={"file": ("f.csv", csv, "text/csv")})
    uid = r.json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})

    import app.main as main
    conn = main.get_connection()
    mid = conn.execute("SELECT id FROM metrics WHERE name='Net Revenue'").fetchone()["id"]
    narrative = "Net Revenue came in at $5.33M for 2025-03, up 12.7% ahead of plan. We added 47 logos."
    conn.execute(
        """INSERT INTO generated_reports (metric_id, period, narrative, sources, confidence,
               faithfulness, prompt_version) VALUES (?, ?, ?, ?, 'High', 'passed', 'test')""",
        (mid, "2025-03", narrative, json.dumps([]), ),
    )
    rid = conn.execute("SELECT MAX(id) AS id FROM generated_reports").fetchone()["id"]
    conn.commit()
    conn.close()

    g = client.get(f"/reports/{rid}/grounding")
    assert g.status_code == 200, g.text
    body = g.json()
    assert any(s["kind"] == "verified" for s in body["sentences"])
    assert 47.0 in body["unverified"]
    assert client.get("/reports/99999/grounding").status_code == 404
