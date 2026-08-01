"""Acceptance tests for the remaining real files in real_testing/ (the five
already covered by test_ingestion_acceptance.py are not repeated here).
Everything is driven through the real upload -> schema -> mapping HTTP
endpoints, and the AR totals are asserted against the figures the scenario
README states independently of this code."""

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
        r = client.post("/ingest/upload", files={"file": (filename, f, mime)})
    assert r.status_code == 200, r.text
    return r.json()


def _schema(client, upload_id, sheet=None):
    params = {"sheet": sheet} if sheet else {}
    r = client.get(f"/ingest/{upload_id}/schema", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _confirm(client, upload_id, mapping):
    r = client.post(f"/ingest/{upload_id}/mapping", json=mapping)
    assert r.status_code == 200, r.text
    return r.json()


AR_TOTALS = {
    "2025-06": 3039999.97,
    "2025-09": 3709999.96,
    "2025-12": 4769999.98,
    "2026-03": 5159999.95,
    "2026-06": 4440000.02,
}


def test_ar_aging_uses_sheet_as_of_date_and_reconciles(client):
    """Each AR sheet is one balance-sheet date stated only in its title row and
    sheet name; Invoice Date / Due Date are per-invoice attributes. Every sheet
    must land on exactly one period and its invoice amounts must sum to the
    documented AR balance -- the TOTAL row must not be double-counted."""
    up = _upload(client, "finance_ar_aging.xlsx", XLSX_MIME)
    assert up["sheets"] == ["AR Jun-25", "AR Sep-25", "AR Dec-25", "AR Mar-26", "AR Jun-26"]

    for sheet in up["sheets"]:
        schema = _schema(client, up["upload_id"], sheet=sheet)
        mapping = schema["suggested_mapping"]

        assert mapping["period_col"] is None
        assert mapping["period_literal"] in AR_TOTALS
        assert "Invoice Date" in mapping["dimension_cols"]

        result = _confirm(client, up["upload_id"], mapping)
        assert result["periods"] == [mapping["period_literal"]]

        period = result["periods"][0]
        facts = client.get("/facts", params={"period": period}).json()
        total = sum(
            f["value"] for f in facts
            if f.get("has_data") and f["metric"].endswith("Invoice Amount")
        )
        assert total == pytest.approx(AR_TOTALS[period], abs=0.05)


def test_ar_aging_total_row_never_becomes_a_metric(client):
    """The TOTAL row marks itself in Customer, not in the mapped metric column
    (Terms), so a filter that only looks at the metric column lets it through
    as a phantom metric holding the whole sheet's total."""
    up = _upload(client, "finance_ar_aging.xlsx", XLSX_MIME)
    schema = _schema(client, up["upload_id"], sheet="AR Jun-25")
    result = _confirm(client, up["upload_id"], schema["suggested_mapping"])

    for name in result["metrics"]:
        assert not name.lower().startswith("total")
        assert not name.lower().startswith("nan")


def test_web_analytics_missing_month_is_absent_not_zero(client):
    """Jul-2025 is missing from the workbook entirely (GA4 migration, never
    backfilled). It must stay absent rather than being invented as zero, and
    the SUM-formula Total row must not become a metric."""
    up = _upload(client, "marketing_web_analytics.xlsx", XLSX_MIME)
    schema = _schema(client, up["upload_id"], sheet="Sessions by Source")
    result = _confirm(client, up["upload_id"], schema["suggested_mapping"])

    assert "2025-07" not in result["periods"]
    assert "2025-06" in result["periods"] and "2025-08" in result["periods"]
    assert len(result["periods"]) == 17
    assert "Total" not in result["metrics"]
    assert len(result["metrics"]) == 9


def test_customer_master_roster_is_flagged_not_silently_mismapped(client):
    """A roster (one row per customer, no repeating period) has no month-by-month
    history. contract_start is near-unique and churn_period is 85% empty, so
    neither is a reporting period -- the user must be warned rather than handed
    a confident but meaningless mapping."""
    up = _upload(client, "finance_customer_master.csv", CSV_MIME)
    schema = _schema(client, up["upload_id"])

    assert schema["mapping_confidence"] == "low"
    roster = [w for w in schema["mapping_warnings"] if "reference/roster" in w]
    assert roster, schema["mapping_warnings"]
    assert "contract_start" in roster[0]
    assert "churn_period" in roster[0]


def test_attribution_prefers_repeating_period_over_transaction_date(client):
    """created_date holds 467 distinct dates across 1,314 opportunities while
    created_period holds the 18 reporting months. Picking the former would
    shatter the series into hundreds of one-row periods."""
    up = _upload(client, "marketing_opportunity_attribution.csv", CSV_MIME)
    schema = _schema(client, up["upload_id"])

    assert schema["suggested_mapping"]["period_col"] == "created_period"

    result = _confirm(client, up["upload_id"], schema["suggested_mapping"])
    assert len(result["periods"]) == 18


@pytest.mark.parametrize("filename,mime,min_periods", [
    ("finance_arr_movement.csv", CSV_MIME, 18),
    ("finance_headcount_payroll.csv", CSV_MIME, 18),
    ("marketing_campaign_spend.csv", CSV_MIME, 18),
])
def test_remaining_long_files_ingest_cleanly(client, filename, mime, min_periods):
    up = _upload(client, filename, mime)
    schema = _schema(client, up["upload_id"])
    result = _confirm(client, up["upload_id"], schema["suggested_mapping"])

    assert len(result["periods"]) == min_periods
    assert result["rows_normalized"] > 0
    assert result["metrics"]
    for name in result["metrics"]:
        assert not name.lower().startswith("total")
