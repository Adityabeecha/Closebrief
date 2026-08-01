from pathlib import Path

import pytest
from dbharness import use_test_db

REAL = Path(__file__).parent.parent.parent / "real_testing"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv"


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


def _upload(client, filename, mime):
    with open(REAL / filename, "rb") as f:
        return client.post("/ingest/upload", files={"file": (filename, f, mime)}).json()


def _select_all_metrics(client):
    kpis = client.get("/kpis").json()
    pending = [{"source_metric": n, "display_name": n, "category": "Marketing",
                "unit": "USD", "direction_good": "up"} for n in kpis["available"]]
    if pending:
        assert client.post("/kpis", json={"kpis": pending}).status_code == 200


def _ingest_funnel(client):
    up = _upload(client, "marketing_funnel_monthly.csv", CSV_MIME)
    schema = client.get(f"/ingest/{up['upload_id']}/schema").json()
    result = client.post(f"/ingest/{up['upload_id']}/mapping",
                         json=schema["suggested_mapping"]).json()
    _select_all_metrics(client)
    return result


def _fact(client, period, metric):
    for f in client.get("/facts", params={"period": period}).json():
        if f["metric"] == metric:
            return f
    return None


def _derived(client, name, formula, unit="USD", direction_good="up"):
    r = client.post("/kpis/derived", json={
        "name": name, "formula": formula, "unit": unit, "direction_good": direction_good,
    })
    assert r.status_code == 201, r.text


def test_m1_cpc_inflation_is_expressible_as_a_ratio_kpi(client):
    _ingest_funnel(client)
    _derived(client, "Paid Search CPC",
             "[Paid Search spend_usd] / [Paid Search clicks]", unit="USD",
             direction_good="down")

    jan25 = _fact(client, "2025-01", "Paid Search CPC")
    feb26 = _fact(client, "2026-02", "Paid Search CPC")
    assert jan25["has_data"] and feb26["has_data"]
    assert jan25["value"] == pytest.approx(5.90, abs=0.05)
    assert feb26["value"] == pytest.approx(11.40, abs=0.05)


def test_m1_spend_flat_while_clicks_and_mqls_halve(client):
    _ingest_funnel(client)

    spend_jan = _fact(client, "2025-01", "Paid Search spend_usd")["value"]
    spend_feb = _fact(client, "2026-02", "Paid Search spend_usd")["value"]
    assert abs(spend_feb - spend_jan) / spend_jan < 0.10

    clicks_jan = _fact(client, "2025-01", "Paid Search clicks")["value"]
    clicks_feb = _fact(client, "2026-02", "Paid Search clicks")["value"]
    assert clicks_jan == pytest.approx(16896, abs=1)
    assert clicks_feb == pytest.approx(8878, abs=1)

    mqls_jan = _fact(client, "2025-01", "Paid Search mqls")["value"]
    mqls_feb = _fact(client, "2026-02", "Paid Search mqls")["value"]
    assert mqls_jan == pytest.approx(163, abs=1)
    assert mqls_feb == pytest.approx(82, abs=1)


def test_clicks_is_not_auto_routed_to_quantity(client):
    up = _upload(client, "marketing_funnel_monthly.csv", CSV_MIME)
    schema = client.get(f"/ingest/{up['upload_id']}/schema").json()

    roles = {c["column_name"]: c["guessed_role"] for c in schema["columns"]}
    assert roles["clicks"] == "measure"
    assert schema["suggested_mapping"].get("quantity_col") is None
    assert schema["suggested_mapping"].get("price_col") is None


def test_pvm_bridge_needs_budget_quantity_which_this_file_lacks(client):
    up = _upload(client, "marketing_funnel_monthly.csv", CSV_MIME)
    schema = client.get(f"/ingest/{up['upload_id']}/schema").json()

    mapping = dict(schema["suggested_mapping"])
    mapping["value_cols"] = []
    mapping["value_col"] = "spend_usd"
    mapping["quantity_col"] = "clicks"
    mapping["dimension_cols"] = [c for c in mapping["dimension_cols"] if c != "clicks"]

    r = client.post(f"/ingest/{up['upload_id']}/mapping", json=mapping)
    assert r.status_code == 200, r.text
    _select_all_metrics(client)

    fact = _fact(client, "2026-02", "Paid Search")
    assert fact is not None and fact["has_data"]
    assert fact.get("variance_bridge") is None


def test_m5_content_volume_up_quality_down(client):
    _ingest_funnel(client)
    _derived(client, "Content MQL to SQL %",
             "[Content/SEO sqls] / [Content/SEO mqls] * 100", unit="%")

    dec = _fact(client, "2025-12", "Content/SEO mqls")["value"]
    jan = _fact(client, "2026-01", "Content/SEO mqls")["value"]
    assert jan / dec > 2.5

    sql_dec = _fact(client, "2025-12", "Content/SEO sqls")["value"]
    sql_jan = _fact(client, "2026-01", "Content/SEO sqls")["value"]
    assert (sql_jan / sql_dec) < (jan / dec) / 2

    rate_dec = _fact(client, "2025-12", "Content MQL to SQL %")["value"]
    rate_jan = _fact(client, "2026-01", "Content MQL to SQL %")["value"]
    assert rate_dec == pytest.approx(8.7, abs=0.2)
    assert rate_jan == pytest.approx(4.2, abs=0.2)

    rate_apr = _fact(client, "2026-04", "Content MQL to SQL %")["value"]
    assert rate_apr > 9.0


def test_m2_channel_efficiency_spread_is_computable(client):
    _ingest_funnel(client)
    _derived(client, "Webinars MQL to SQL %",
             "[Webinars sqls] / [Webinars mqls] * 100", unit="%")
    _derived(client, "Partner MQL to SQL %",
             "[Partner Co-marketing sqls] / [Partner Co-marketing mqls] * 100", unit="%")

    webinars = [_fact(client, p, "Webinars MQL to SQL %") for p in ("2025-06", "2026-01")]
    partner = [_fact(client, p, "Partner MQL to SQL %") for p in ("2025-06", "2026-01")]

    assert all(f is not None and f["has_data"] for f in webinars + partner)
    assert max(f["value"] for f in webinars) < min(f["value"] for f in partner)


def test_m6_missing_month_leaves_a_gap_not_a_shift(client):
    up = _upload(client, "marketing_web_analytics.xlsx", XLSX_MIME)
    schema = client.get(f"/ingest/{up['upload_id']}/schema",
                        params={"sheet": "Sessions by Source"}).json()
    result = client.post(f"/ingest/{up['upload_id']}/mapping",
                         json=schema["suggested_mapping"]).json()
    _select_all_metrics(client)

    assert "2025-07" not in result["periods"]
    assert "2025-06" in result["periods"] and "2025-08" in result["periods"]

    jul = _fact(client, "2025-07", "Paid Search")
    assert jul is None or not jul["has_data"]

    aug = _fact(client, "2025-08", "Paid Search")
    assert aug["has_data"]
    assert aug["prior_value"] is None or aug["deltas"]["mom_pct"] is None


def test_m6_paid_search_sessions_decline_corroborating_m1(client):
    up = _upload(client, "marketing_web_analytics.xlsx", XLSX_MIME)
    schema = client.get(f"/ingest/{up['upload_id']}/schema",
                        params={"sheet": "Sessions by Source"}).json()
    client.post(f"/ingest/{up['upload_id']}/mapping", json=schema["suggested_mapping"])
    _select_all_metrics(client)

    jan25 = _fact(client, "2025-01", "Paid Search")["value"]
    feb26 = _fact(client, "2026-02", "Paid Search")["value"]
    assert feb26 < jan25
