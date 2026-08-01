from pathlib import Path

import pytest
from dbharness import use_test_db

REAL = Path(__file__).parent / "fixtures" / "ingestion"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv"

REVENUE = ("[Subscription Revenue] + [Professional Services Revenue] "
           "+ [Training & Certification Revenue]")
COGS = ("[Cloud Hosting & Infrastructure] + [Third-Party Software Licenses (COGS)] "
        "+ [Customer Support Salaries & Benefits] + [Payment Processing Fees] "
        "+ [Contractors - Professional Services]")
OPEX = ("[Sales Salaries & Commissions] + [Marketing Salaries & Benefits] "
        "+ [Marketing Programs & Advertising] + [Travel & Entertainment - Sales] "
        "+ [Sales Tools & CRM] + [Engineering Salaries & Benefits] "
        "+ [Contractors - Engineering] + [Product Salaries & Benefits] "
        "+ [Development Tools & Cloud (non-prod)] + [G&A Salaries & Benefits] "
        "+ [Rent & Facilities] + [Legal & Professional Fees] "
        "+ [Audit, Tax & Compliance] + [Insurance] + [Bad Debt Expense] "
        "+ [Corporate SaaS & IT] + [Customer Success Salaries & Benefits]")


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


def _ingest_gl(client):
    with open(REAL / "finance_gl_actuals.csv", "rb") as f:
        up = client.post("/ingest/upload",
                         files={"file": ("finance_gl_actuals.csv", f, CSV_MIME)}).json()
    schema = client.get(f"/ingest/{up['upload_id']}/schema").json()
    return client.post(f"/ingest/{up['upload_id']}/mapping",
                       json=schema["suggested_mapping"]).json()


def _select_all_metrics(client):
    kpis = client.get("/kpis").json()
    pending = [{"source_metric": n, "display_name": n, "category": "GL",
                "unit": "USD", "direction_good": "up"} for n in kpis["available"]]
    if pending:
        r = client.post("/kpis", json={"kpis": pending})
        assert r.status_code == 200, r.text


def _gl_with_budget(client):
    gl = _ingest_gl(client)
    with open(REAL / "finance_budget_plan.xlsx", "rb") as f:
        bud = client.post("/ingest/upload",
                          files={"file": ("finance_budget_plan.xlsx", f, XLSX_MIME)}).json()
    for sheet in ("FY2025 Plan", "FY2026 Plan"):
        bschema = client.get(f"/ingest/{bud['upload_id']}/schema", params={"sheet": sheet}).json()
        r = client.post(f"/ingest/{bud['upload_id']}/join-budget", json={
            "target_dataset_id": gl["dataset_id"],
            "mapping": bschema["suggested_mapping"],
        })
        assert r.status_code == 200, r.text
    _select_all_metrics(client)
    return gl


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


def test_f3_settlement_flags_as_anomaly_with_large_mom(client):
    _gl_with_budget(client)

    aug = _fact(client, "2025-08", "Legal & Professional Fees")
    assert aug["has_data"]
    assert aug["value"] == pytest.approx(1388577.82, abs=1.0)
    assert aug["is_anomaly"] is True
    assert aug["deltas"]["mom_pct"] == pytest.approx(517.6, abs=1.0)


def test_budget_in_this_dataset_carries_no_real_variance(client):
    _gl_with_budget(client)

    worst_abs = worst_pct = 0.0
    for period in [f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 7)]:
        for fact in client.get("/facts", params={"period": period}).json():
            var_abs = fact["deltas"].get("budget_var_abs")
            var_pct = fact["deltas"].get("budget_var_pct")
            if var_abs is not None:
                worst_abs = max(worst_abs, abs(var_abs))
            if var_pct is not None:
                worst_pct = max(worst_pct, abs(var_pct))

    assert worst_abs <= 250.0
    assert worst_pct < 5.0


def test_f3_settlement_does_not_rank_on_budget_variance(client):
    _gl_with_budget(client)

    facts = client.get("/facts", params={"period": "2025-08"}).json()
    ranked = sorted(
        (f for f in facts if f["deltas"].get("budget_var_abs") is not None),
        key=lambda f: abs(f["deltas"]["budget_var_abs"]), reverse=True,
    )
    assert ranked[0]["metric"] != "Legal & Professional Fees"
    assert abs(ranked[0]["deltas"]["budget_var_abs"]) < 1000

    assert "Legal & Professional Fees" in {f["metric"] for f in facts if f["is_anomaly"]}


def test_f2_contractor_reclass_combines_into_one_smooth_series(client):
    _gl_with_budget(client)
    _derived(client, "Contractors - Total",
             "[Contractors - Engineering] + [Contractors - Professional Services]")

    series = {}
    for period in ("2025-08", "2025-09", "2025-10", "2025-11"):
        fact = _fact(client, period, "Contractors - Total")
        assert fact is not None and fact["has_data"], f"no combined value for {period}"
        series[period] = fact["value"]

    step = abs(series["2025-10"] - series["2025-09"]) / series["2025-09"]
    assert step < 0.25, f"combined series should be smooth across the reclass, moved {step:.1%}"


def test_f2_components_step_hard_while_combined_does_not(client):
    _gl_with_budget(client)
    _derived(client, "Contractors - Total",
             "[Contractors - Engineering] + [Contractors - Professional Services]")

    eng_sep = _fact(client, "2025-09", "Contractors - Engineering")
    eng_oct = _fact(client, "2025-10", "Contractors - Engineering")
    ps_sep = _fact(client, "2025-09", "Contractors - Professional Services")
    ps_oct = _fact(client, "2025-10", "Contractors - Professional Services")

    assert eng_sep["has_data"] and eng_sep["value"] > 100000
    assert not eng_oct["has_data"]
    assert not ps_sep["has_data"]
    assert ps_oct["has_data"] and ps_oct["value"] > 100000


def test_f1_gross_margin_erodes_then_recovers(client):
    _gl_with_budget(client)
    _derived(client, "Gross Margin %",
             f"(({REVENUE}) - ({COGS})) / ({REVENUE}) * 100", unit="%")

    gm = {}
    for period in ("2025-01", "2025-07", "2025-11", "2026-02", "2026-06"):
        fact = _fact(client, period, "Gross Margin %")
        assert fact is not None and fact["has_data"], period
        gm[period] = fact["value"]

    assert gm["2025-01"] == pytest.approx(81.8, abs=0.5)
    assert gm["2025-07"] == pytest.approx(81.7, abs=0.5)
    assert gm["2025-11"] == pytest.approx(72.4, abs=0.5)
    assert gm["2026-02"] == pytest.approx(71.2, abs=0.5)
    assert gm["2026-06"] == pytest.approx(77.2, abs=0.5)


def test_f10_ebitda_does_not_improve_while_revenue_grows(client):
    _gl_with_budget(client)
    _derived(client, "EBITDA", f"({REVENUE}) - ({COGS}) - ({OPEX})")

    jan25 = _fact(client, "2025-01", "EBITDA")
    jun26 = _fact(client, "2026-06", "EBITDA")
    assert jan25["value"] == pytest.approx(-407194, rel=0.02)
    assert jun26["value"] == pytest.approx(-542864, rel=0.02)
    assert jun26["value"] < jan25["value"]

    rev_jan = _fact(client, "2025-01", "Subscription Revenue")["value"]
    rev_jun = _fact(client, "2026-06", "Subscription Revenue")["value"]
    assert rev_jun / rev_jan > 1.3


def test_bad_debt_spike_is_an_anomaly(client):
    _gl_with_budget(client)

    mar = _fact(client, "2026-03", "Bad Debt Expense")
    assert mar["has_data"]
    assert mar["value"] > 250000
    assert mar["is_anomaly"] is True

    feb = _fact(client, "2026-02", "Bad Debt Expense")
    assert mar["value"] / feb["value"] > 10


def test_hosting_compounds_then_drops_after_rearchitecture(client):
    _gl_with_budget(client)

    jul = _fact(client, "2025-07", "Cloud Hosting & Infrastructure")["value"]
    nov = _fact(client, "2025-11", "Cloud Hosting & Infrastructure")["value"]
    dec = _fact(client, "2025-12", "Cloud Hosting & Infrastructure")["value"]

    assert nov > jul * 1.5
    assert dec < nov
