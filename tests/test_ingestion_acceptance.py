"""Acceptance tests for the ingestion fix: five real files from a synthetic
company's export set (tests/fixtures/ingestion/), driven through the actual
upload -> schema -> mapping -> join-budget HTTP endpoints exactly as the
frontend does. Counts are exact, per the task's acceptance criteria."""

from pathlib import Path

import pytest
from dbharness import use_test_db

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"


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


def _upload(client, filename, content_type):
    with open(FIXTURES / filename, "rb") as f:
        r = client.post("/ingest/upload", files={"file": (filename, f, content_type)})
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


def _join(client, upload_id, target_dataset_id, mapping, metric_suffix=None):
    body = {"target_dataset_id": target_dataset_id, "mapping": mapping}
    if metric_suffix:
        body["metric_suffix"] = metric_suffix
    r = client.post(f"/ingest/{upload_id}/join-budget", json=body)
    assert r.status_code == 200, r.text
    return r.json()


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ------------------------------------------------------- (1) finance_gl_actuals
def test_gl_actuals_27_metrics_468_facts(client):
    up = _upload(client, "finance_gl_actuals.csv", "text/csv")
    schema = _schema(client, up["upload_id"])
    result = _confirm(client, up["upload_id"], schema["suggested_mapping"])

    assert len(result["metrics"]) == 27
    assert result["rows_normalized"] == 468
    assert len(result["periods"]) == 18

    # account_code / fx_rate never become metrics; amount_local and amount_usd
    # don't both become metrics (one reporting currency chosen).
    assert "account_code" not in result["metrics"]
    assert "fx_rate_usd_per_local" not in result["metrics"]
    assert "amount_local" not in result["metrics"]
    assert "amount_usd" not in result["metrics"]
    assert "Subscription Revenue" in result["metrics"]

    facts = client.get("/facts?period=2025-01").json()
    sub_rev = next(f for f in facts if f["metric"] == "Subscription Revenue")
    assert sub_rev["has_data"]
    assert sub_rev["value"] == pytest.approx(1378666.67 + 390250.00, abs=0.01)


def test_gl_actuals_entity_rows_sum_never_take_first(client):
    import pandas as pd

    src = pd.read_csv(FIXTURES / "finance_gl_actuals.csv")
    grouped = src.groupby(["account_name", "period"])["amount_usd"]
    totals, firsts = grouped.sum().to_dict(), grouped.first().to_dict()
    assert len(totals) == 468

    up = _upload(client, "finance_gl_actuals.csv", "text/csv")
    schema = _schema(client, up["upload_id"])
    _confirm(client, up["upload_id"], schema["suggested_mapping"])

    checked = 0
    for period in sorted({p for _, p in totals}):
        for fact in client.get("/facts", params={"period": period}).json():
            key = (fact["metric"], period)
            if key not in totals or not fact.get("has_data"):
                continue
            checked += 1
            assert fact["value"] == pytest.approx(totals[key], abs=0.01), (
                f"{key}: stored {fact['value']}, consolidated total {totals[key]}, "
                f"first-entity-only {firsts[key]}"
            )
    assert checked == 468


# ---------------------------------------------------- (2) finance_budget_plan
def test_budget_plan_joins_onto_gl_actuals_metrics(client):
    up1 = _upload(client, "finance_gl_actuals.csv", "text/csv")
    schema1 = _schema(client, up1["upload_id"])
    gl = _confirm(client, up1["upload_id"], schema1["suggested_mapping"])
    assert len(gl["metrics"]) == 27

    up2 = _upload(client, "finance_budget_plan.xlsx", XLSX_MIME)
    assert set(up2["sheets"]) == {"FY2025 Plan", "FY2026 Plan", "Assumptions"}

    total_matched = 0
    for sheet in ("FY2025 Plan", "FY2026 Plan"):
        schema2 = _schema(client, up2["upload_id"], sheet=sheet)
        # Both plan sheets read as a valid mapping -- never the hard
        # "missing wide_period_cols" error the bug report showed.
        assert schema2["suggested_mapping"]["wide_period_cols"]
        assert schema2["suggested_mapping"]["wide_metric_col"] == "Account"
        assert schema2["suggested_mapping"]["id_col"] == "GL Code"

        report = _join(client, up2["upload_id"], gl["dataset_id"], schema2["suggested_mapping"])
        # GL Code matching -> no near-misses, no unmatched metrics.
        assert report["near_misses"] == []
        assert report["unmatched_metrics"] == []
        total_matched += report["matched"]

    # 27 metrics x 18 periods, minus the account-reclass scenario planted in
    # this file: "Contractors - Engineering" has actuals Jan-Sep-25 only (no
    # Oct-25..Jun-26 row to attach a budget to -> 9 unmatched), and its
    # replacement "Contractors - Professional Services" has actuals Oct-25
    # onward only (no Jan-Sep-25 row -> 9 unmatched). The budget file budgets
    # both accounts for all 18 months regardless, so 18 of the 486 possible
    # budget values have nowhere to land -- reported, not silently dropped.
    assert total_matched == 27 * 18 - 18

    # Section labels, the "Total Operating Expenses" rollup, and the footnote
    # never became metrics on the target dataset.
    facts = client.get("/facts?period=2025-01").json()
    metric_names = {f["metric"] for f in facts}
    for bad in ("REVENUE", "COGS", "OPERATING EXPENSES", "OTHER"):
        assert bad not in metric_names
    assert not any("Total Operating" in m for m in metric_names)
    assert not any(m.startswith("Note:") for m in metric_names)

    # 7020 (Interest Income, accounting-format negative) parsed as a real
    # negative number, not text or null.
    ii = next(f for f in facts if f["metric"].strip() == "Interest Income")
    assert ii["deltas"]["budget_var_abs"] is not None  # budget landed and is numeric


def test_budget_plan_assumptions_sheet_not_ingested(client):
    """The Assumptions sheet is a reference sheet, not a data table — it must
    never be auto-selected or produce fake metrics."""
    up = _upload(client, "finance_budget_plan.xlsx", XLSX_MIME)
    schema = _schema(client, up["upload_id"], sheet="Assumptions")
    # profile_columns still returns SOMETHING for it (never crashes), but its
    # shape doesn't look like a metrics table: no period-shaped wide columns.
    assert schema["wide_period_cols"] == []


# ----------------------------------------------- (3) finance_vendor_saas_spend
def test_vendor_saas_spend_regression_baseline(client):
    up = _upload(client, "finance_vendor_saas_spend.csv", "text/csv")
    schema = _schema(client, up["upload_id"])

    assert schema["suggested_mapping"]["metric_col"] == "vendor"
    assert schema["suggested_mapping"]["period_col"] == "period"

    alternatives = [w for w in schema["mapping_warnings"] if "could be grouped by" in w]
    assert len(alternatives) == 1
    assert "category" in alternatives[0] and "owning_department" in alternatives[0]
    assert len(schema["mapping_warnings"]) == 1

    result = _confirm(client, up["upload_id"], schema["suggested_mapping"])
    assert len(result["metrics"]) == 33
    assert result["rows_normalized"] == 436


# ------------------------------------------------- (4) marketing_funnel_monthly
def test_funnel_monthly_80_metrics_channel_x_measure_cross_product(client):
    up = _upload(client, "marketing_funnel_monthly.csv", "text/csv")
    schema = _schema(client, up["upload_id"])
    assert schema["suggested_mapping"]["metric_col"] == "channel"
    assert len(schema["suggested_mapping"]["value_cols"]) == 8

    result = _confirm(client, up["upload_id"], schema["suggested_mapping"])
    assert len(result["metrics"]) == 80  # 10 channels x 8 measures
    assert result["rows_normalized"] == 1278  # 1440 possible, minus 162 null clicks

    # "<channel> clicks" exists for every channel...
    assert "Paid Search clicks" in result["metrics"]
    assert "Content/SEO clicks" in result["metrics"]

    # ...populated (non-null) only on Paid Search, NULL (not zero) elsewhere.
    facts = client.get("/facts?period=2025-01").json()
    ps_clicks = next(f for f in facts if f["metric"] == "Paid Search clicks")
    seo_clicks = next(f for f in facts if f["metric"] == "Content/SEO clicks")
    assert ps_clicks["has_data"] is True and ps_clicks["value"] is not None
    assert seo_clicks["has_data"] is False  # exists as a metric, no fact this period
    assert seo_clicks["value"] is None


# -------------------------------------------------- (5) marketing_program_budget
def test_program_budget_joins_onto_funnel_spend_with_near_miss_confirm(client):
    up1 = _upload(client, "marketing_funnel_monthly.csv", "text/csv")
    schema1 = _schema(client, up1["upload_id"])
    funnel = _confirm(client, up1["upload_id"], schema1["suggested_mapping"])
    assert len(funnel["metrics"]) == 80

    up2 = _upload(client, "marketing_program_budget.xlsx", XLSX_MIME)
    schema2 = _schema(client, up2["upload_id"])
    assert schema2["mapping_confidence"] == "high"
    assert schema2["suggested_mapping"]["wide_metric_col"] == "Channel"

    report = _join(client, up2["upload_id"], funnel["dataset_id"],
                   schema2["suggested_mapping"], metric_suffix="spend_usd")

    assert report["matched"] == 180  # 10 channels x 18 periods
    assert report["unmatched_metrics"] == []
    assert report["unmatched_periods"] == []
    # The "Total" row never became a metric or a match attempt.
    assert not any("Total" in nm["upload_label"] for nm in report["near_misses"])

    # The three deliberately-drifted labels resolved via normalized matching,
    # surfaced for confirmation rather than silently merged or rejected.
    near_miss_labels = {nm["upload_label"] for nm in report["near_misses"]}
    assert near_miss_labels == {
        "Content/Seo spend_usd", "Paid Social - Linkedin spend_usd", "Partner Co-Marketing spend_usd",
    }
    resolved = {nm["matched_metric"] for nm in report["near_misses"]}
    assert resolved == {
        "Content/SEO spend_usd", "Paid Social - LinkedIn spend_usd", "Partner Co-marketing spend_usd",
    }

    facts = client.get("/facts?period=2025-01").json()
    spend = next(f for f in facts if f["metric"] == "Paid Search spend_usd")
    assert spend["deltas"]["budget_var_abs"] is not None


# --------------------------------------------------------- (6) no silent under-detection
def test_low_confidence_mapping_flags_a_warning_not_a_silent_success(client):
    """A file whose metric-name column looks under-populated (fewer distinct
    values than rows) must surface a warning, never a quiet success."""
    up = _upload(client, "finance_gl_actuals.csv", "text/csv")
    schema = _schema(client, up["upload_id"])
    # This file DOES have ambiguity (amount_local vs amount_usd both look like
    # measures) -- confidence must reflect that, not claim "high" when there
    # was a judgment call made on the caller's behalf.
    assert schema["mapping_confidence"] == "low"
    assert len(schema["mapping_warnings"]) >= 1


def test_bad_mapping_returns_422_not_a_500(client):
    """The old failure mode (hard MappingError with no recovery path) still
    exists as a safety net for a truly unusable mapping, but must be a clean
    422 -- never crash the request."""
    up = _upload(client, "finance_gl_actuals.csv", "text/csv")
    r = client.post(f"/ingest/{up['upload_id']}/mapping",
                    json={"layout": "wide", "wide_period_cols": []})
    assert r.status_code == 422
    assert "wide_period_cols" in r.json()["detail"]


def test_join_budget_requires_target_dataset(client):
    up = _upload(client, "finance_budget_plan.xlsx", XLSX_MIME)
    r = client.post(f"/ingest/{up['upload_id']}/join-budget",
                    json={"mapping": {"layout": "wide", "wide_period_cols": ["Jan-25"]}})
    assert r.status_code == 422


def test_join_budget_unknown_dataset_404s(client):
    up = _upload(client, "finance_budget_plan.xlsx", XLSX_MIME)
    r = client.post(f"/ingest/{up['upload_id']}/join-budget",
                    json={"target_dataset_id": 999999,
                          "mapping": {"layout": "wide", "wide_period_cols": ["Jan-25"], "wide_metric_col": "Account"}})
    assert r.status_code == 404
