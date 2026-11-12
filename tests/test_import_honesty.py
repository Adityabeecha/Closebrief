"""Import reports and availability must expose changes to source figures."""

import pandas as pd
import pytest
from test_datasets import client  # noqa: F401 - shared isolated API fixture

from app.db import get_connection
from app.ingestion.mapping import MappingSpec, normalize

LONG = {"layout": "long", "period_col": "period", "metric_col": "metric", "value_col": "value"}
REASON = ("Combined from 2 rows; percentages can't be added. Import one row per month, "
          "or use a custom KPI to calculate it.")


def _upload(api, text):
    response = api.post("/ingest/upload", files={"file": ("honesty.csv", text, "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()["upload_id"]


def _import(api, text, mapping=None):
    upload_id = _upload(api, text)
    response = api.post(f"/ingest/{upload_id}/mapping", json=mapping or LONG)
    assert response.status_code == 200, response.text
    return response.json()


def test_duplicates_dropped_but_distinct_dimensions_and_ids_combined(client):  # noqa: F811
    result = _import(client,
                     "period,metric,value,budget,region,id,ignored\n"
                     "2025-01,Revenue,100,90,East,A,one\n"
                     "2025-01,Revenue,100,90,East,A,two\n"
                     "2025-01,Revenue,100,90,West,A,three\n"
                     "2025-01,Revenue,100,90,West,B,four\n",
                     {**LONG, "budget_col": "budget", "dimension_cols": ["region"], "id_col": "id"})
    report = result["import_report"]
    assert (report["rows_read"], report["rows_kept"]) == (4, 3)
    assert report["duplicates_dropped"] == {
        "count": 1, "examples": [{"row": 2, "metric": "Revenue", "period": "2025-01"}]}
    assert report["combined"] == [{"metric": "Revenue", "period": "2025-01", "rows": 3}]
    conn = get_connection()
    try:
        row = conn.execute("SELECT value, budget, source_rows FROM metric_values").fetchone()
        assert tuple(row) == (300, 270, 3)
    finally:
        conn.close()


def test_wide_duplicates_are_source_rows_not_melted_cells(client):  # noqa: F811
    result = _import(client, "metric,Jan-25,Feb-25\nRevenue,100,110\nRevenue,100,110\nRevenue,100,120\n",
                     {"layout": "wide", "wide_metric_col": "metric", "wide_period_cols": ["Jan-25", "Feb-25"]})
    report = result["import_report"]
    assert (report["rows_read"], report["rows_kept"], report["duplicates_dropped"]["count"]) == (3, 2, 1)
    assert report["combined"] == [
        {"metric": "Revenue", "period": "2025-01", "rows": 2},
        {"metric": "Revenue", "period": "2025-02", "rows": 2},
    ]


@pytest.mark.parametrize("granularity,period", [("month", "2025-01"), ("quarter", "2025-Q1"), ("year", "2025")])
def test_combined_percent_unavailable_but_currency_still_sums(client, granularity, period):  # noqa: F811
    _import(client, "period,metric,value\n2025-01,Margin,20\n2025-01,Margin,30\n"
                    "2025-01,Revenue,100\n2025-01,Revenue,200\n")
    response = client.post("/kpis", json={"kpis": [
        {"source_metric": name, "display_name": name, "category": "Test", "unit": unit, "direction_good": "up"}
        for name, unit in [("Margin", "%"), ("Revenue", "USD")]
    ]})
    assert response.status_code == 200, response.text
    facts = client.get("/facts", params={"period": period, "granularity": granularity}).json()
    by_name = {f["metric"]: f for f in facts}
    assert by_name["Margin"]["has_data"] is False
    assert by_name["Margin"]["unavailable_reason"] == REASON
    assert by_name["Margin"]["value"] is None
    assert by_name["Margin"]["chart_data"] is None
    assert by_name["Revenue"]["has_data"] is True
    assert by_name["Revenue"]["value"] == 300
    close = client.get("/close/months").json()[0]
    assert close["kpis"] == 1
    digest = client.get("/digest?period=2025-01").json()
    assert digest["total_kpis"] == 1
    assert digest["pending"] == ["Revenue"]


def test_schema_warns_how_many_rows_will_be_added(client):  # noqa: F811
    upload = _upload(client, "period,metric,value,region\n2025-01,Revenue,100,East\n2025-01,Revenue,200,West\n")
    schema = client.get(f"/ingest/{upload}/schema").json()
    assert any("2 rows" in w and "ADDED together" in w for w in schema["mapping_warnings"])


def test_duplicate_report_examples_and_combined_list_are_capped(client):  # noqa: F811
    rows = "".join(f"2025-01,Metric {i},1\n2025-01,Metric {i},1\n2025-01,Metric {i},2\n" for i in range(55))
    report = _import(client, "period,metric,value\n" + rows)["import_report"]
    assert report["duplicates_dropped"]["count"] == 55
    assert len(report["duplicates_dropped"]["examples"]) == 20
    assert len(report["combined"]) == 50


def test_numeric_coercion_does_not_make_different_source_rows_duplicates():
    frame = pd.DataFrame({"period": ["Jan-25", "2025-01"], "metric": ["Revenue", "Revenue"], "value": ["100", "100.0"]})
    canonical = normalize(frame, MappingSpec(**LONG))
    assert len(canonical) == 2


def test_duplicate_row_number_survives_empty_source_rows(client):  # noqa: F811
    result = _import(client, "period,metric,value\n2025-01,Revenue,100\n,,\n2025-01,Revenue,100\n")
    assert result["import_report"]["duplicates_dropped"]["examples"] == [
        {"row": 3, "metric": "Revenue", "period": "2025-01"}]


def test_invalid_percentage_history_is_not_used_in_valid_month(client):  # noqa: F811
    _import(client, "period,metric,value\n2025-01,Margin,20\n2025-01,Margin,30\n2025-02,Margin,40\n")
    response = client.post("/kpis", json={"kpis": [
        {"source_metric": "Margin", "display_name": "Margin", "category": "Test", "unit": "%", "direction_good": "up"}
    ]})
    assert response.status_code == 200, response.text
    fact = client.get("/facts?period=2025-02").json()[0]
    assert fact["has_data"] and fact["value"] == 40
    assert fact["prior_value"] is None
    assert fact["deltas"]["mom_pct"] is None
    assert [r["period"] for r in fact["chart_data"]["trend"]] == ["2025-02"]
