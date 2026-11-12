"""Fiscal keys name the ending year and all views use the same underlying months."""

import pytest
from test_datasets import client as client
from test_import_honesty import _import

from app.compute import aggregate
from app.db import get_connection


@pytest.mark.parametrize("month,quarter,year", [
    ("2025-04", "2026-Q1", "2026"), ("2025-06", "2026-Q1", "2026"),
    ("2026-03", "2026-Q4", "2026"), ("2026-04", "2027-Q1", "2027"),
])
def test_april_fiscal_keys(month, quarter, year):
    assert aggregate.period_key(month, "quarter", 4) == quarter
    assert aggregate.period_key(month, "year", 4) == year
    assert aggregate.period_key(month, "month", 4) == month


def test_fiscal_months_and_comparison_keys():
    assert aggregate.months_in("2026-Q1", "quarter", 4) == ["2025-04", "2025-05", "2025-06"]
    assert aggregate.months_in("2026-Q4", "quarter", 4) == ["2026-01", "2026-02", "2026-03"]
    assert aggregate.months_in("2026", "year", 4) == [
        "2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09",
        "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]
    assert aggregate.prior_key("2026-Q1", "quarter", 4) == "2025-Q4"
    assert aggregate.year_ago_key("2026-Q1", "quarter", 4) == "2025-Q1"
    assert aggregate.prior_key("2026", "year", 4) == "2025"
    assert aggregate.year_ago_key("2026", "year", 4) == "2025"
    assert aggregate.months_in("2026-Q3", "quarter", 7) == ["2026-01", "2026-02", "2026-03"]


def test_calendar_default_is_unchanged():
    for month in range(1, 13):
        raw = f"2025-{month:02d}"
        for granularity in aggregate.GRANULARITIES:
            assert aggregate.period_key(raw, granularity) == aggregate.period_key(raw, granularity, 1)
    assert aggregate.months_in("2025-Q1", "quarter", 1) == ["2025-01", "2025-02", "2025-03"]
    assert aggregate.sort_key("2025-Q1") == (2025, 1)


def test_fiscal_api_facts_close_targets_and_activity(client):
    from app import audit

    ds = _import(client, "period,metric,value\n2024-04,Revenue,10\n2025-01,Revenue,5\n2025-03,Revenue,5\n"
                        "2025-04,Revenue,20\n2025-05,Revenue,30\n2025-06,Revenue,40\n2026-03,Revenue,50\n2026-04,Revenue,99\n")["dataset_id"]
    response = client.patch(f"/datasets/{ds}", json={"fiscal_year_start": 4})
    assert response.status_code == 200, response.text
    assert response.json()["fiscal_year_start"] == 4
    assert client.get("/datasets").json()["fiscal_year_start"] == 4
    assert client.get("/datasets").json()["datasets"][0]["fiscal_year_start"] == 4
    assert client.post(f"/datasets/{ds}/activate").json()["fiscal_year_start"] == 4
    assert client.get("/domain").json()["fiscal_year_start"] == 4
    assert client.get("/kpis").json()["fiscal_year_start"] == 4
    assert client.get("/periods?granularity=quarter").json() == ["2025-Q1", "2025-Q4", "2026-Q1", "2026-Q4", "2027-Q1"]
    quarter = client.get("/facts?period=2026-Q1&granularity=quarter").json()[0]
    assert quarter["fiscal_year_start"] == 4
    assert quarter["value"] == 90
    assert quarter["deltas"]["mom_pct"] == quarter["deltas"]["yoy_pct"] == 800
    assert client.get("/facts?period=2026&granularity=year").json()[0]["value"] == 140
    assert client.get("/facts?period=2025-04").json()[0]["fiscal_year_start"] == 4
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id = ?", (ds,)).fetchone()["id"]
        for period, status in [("2025-04", "approved"), ("2025-05", "pending"), ("2025-03", "approved")]:
            rid = conn.execute("INSERT INTO generated_reports (metric_id, period, narrative, review_status, faithfulness, confidence) "
                               "VALUES (?, ?, 'Reviewed.', ?, 'passed', 'High')", (mid, period, status)).lastrowid
            conn.commit()
            audit.record(conn, "approve", "report", rid)
    finally:
        conn.close()
    summary = next(r for r in client.get("/close/months?granularity=quarter").json() if r["period"] == "2026-Q1")
    assert (summary["kpis"], summary["drafted"], summary["approved"], summary["month_count"]) == (3, 2, 1, 3)
    status = client.get("/close/status?period=2026-Q1").json()
    steps = {s["id"]: s for s in status["steps"]}
    assert steps["review"]["target"].startswith("#month=2025-06&")
    assert steps["approve"]["target"].startswith("#month=2025-05&")
    # Two approvals in this fiscal quarter, plus the import of the dataset itself.
    assert len(status["activity"]) == 3
    assert sum(entry["text"] == "Import dataset" for entry in status["activity"]) == 1
    assert all("2025-03" not in entry["text"] for entry in status["activity"])


@pytest.mark.parametrize("value", [0, 13, None, True, "4", 4.5])
def test_fiscal_start_rejects_invalid_values(client, value):
    ds = _import(client, "period,metric,value\n2025-04,Revenue,10\n")["dataset_id"]
    assert client.patch(f"/datasets/{ds}", json={"fiscal_year_start": value}).status_code == 422
