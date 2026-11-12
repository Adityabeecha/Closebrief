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
    # No ID column: identical rows may be real repeats, so they're kept (added) and reported.
    assert (report["rows_read"], report["rows_kept"], report["duplicates_dropped"]["count"]) == (3, 3, 0)
    assert report["identical_kept"]["count"] == 1
    assert report["combined"] == [
        {"metric": "Revenue", "period": "2025-01", "rows": 3},
        {"metric": "Revenue", "period": "2025-02", "rows": 3},
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
    assert report["duplicates_dropped"]["count"] == 0
    assert report["identical_kept"]["count"] == 55
    assert len(report["identical_kept"]["examples"]) == 20
    assert len(report["combined"]) == 50


def test_numeric_coercion_does_not_make_different_source_rows_duplicates():
    frame = pd.DataFrame({"period": ["Jan-25", "2025-01"], "metric": ["Revenue", "Revenue"], "value": ["100", "100.0"]})
    canonical = normalize(frame, MappingSpec(**LONG))
    assert len(canonical) == 2


def test_duplicate_row_number_survives_empty_source_rows(client):  # noqa: F811
    result = _import(client, "period,metric,value\n2025-01,Revenue,100\n,,\n2025-01,Revenue,100\n")
    assert result["import_report"]["identical_kept"]["examples"] == [
        {"row": 3, "metric": "Revenue", "period": "2025-01"}]


def test_identical_rows_without_an_id_are_both_counted(client):  # noqa: F811
    # Two $80 web sales in the same month look identical: dropping one would
    # understate revenue. Only rows sharing a row ID are certainly duplicates.
    result = _import(client, "period,metric,value,channel\n2025-01,Sales,80,web\n2025-01,Sales,80,web\n",
                     {**LONG, "dimension_cols": ["channel"]})
    report = result["import_report"]
    assert report["duplicates_dropped"]["count"] == 0 and report["identical_kept"]["count"] == 1
    conn = get_connection()
    try:
        assert tuple(conn.execute("SELECT value, source_rows FROM metric_values").fetchone()) == (160, 2)
    finally:
        conn.close()


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


@pytest.mark.parametrize("label,kind,example", [
    ("Q1 2025", "quarterly", "Q1 2025"), ("2025 Q2", "quarterly", "Q1 2025"), ("FY24", "yearly", "FY24"),
])
@pytest.mark.parametrize("layout", ["long", "wide", "literal"])
def test_coarse_period_import_refused_without_creating_dataset(client, label, kind, example, layout):  # noqa: F811
    if layout == "wide":
        text = f"metric,{label}\nRevenue,100\n"
        mapping = {"layout": "wide", "wide_metric_col": "metric", "wide_period_cols": [label]}
    else:
        text = f"period,metric,value\n{label},Revenue,100\n"
        mapping = dict(LONG)
        if layout == "literal":
            mapping.update(period_col=None, period_literal=label)
    upload_id = _upload(client, text)
    response = client.post(f"/ingest/{upload_id}/mapping", json=mapping)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == (
        f"This file has {kind} figures (e.g. {example}). CloseBrief works month by month: "
        "export monthly figures instead. Quarters and years are added up for you in the Q and Y views."
    )
    assert client.get("/datasets").json()["datasets"] == []


@pytest.mark.parametrize("label,period", [
    ("Jan-25", "2025-01"), ("2025-03", "2025-03"), ("03/2025", "2025-03"), ("2025-03-15", "2025-03"),
])
@pytest.mark.parametrize("layout", ["long", "wide", "literal"])
def test_month_labels_and_full_dates_still_import(client, label, period, layout):  # noqa: F811
    if layout == "wide":
        text = f"metric,{label}\nRevenue,100\n"
        mapping = {"layout": "wide", "wide_metric_col": "metric", "wide_period_cols": [label]}
    else:
        text = f"period,metric,value\n{label},Revenue,100\n"
        mapping = dict(LONG)
        if layout == "literal":
            mapping.update(period_col=None, period_literal=label)
    result = _import(client, text, mapping)
    assert result["periods"] == [period]


@pytest.mark.parametrize("text,kind", [
    ("period,metric,value\nQ1 2025,Revenue,100\n2025 Q2,Revenue,110\n", "quarterly"),
    ("period,metric,value\nFY24,Revenue,100\nFY25,Revenue,110\n", "yearly"),
    ("year,metric,value\n2024,Revenue,100\n2025,Revenue,110\n", "yearly"),
    ("metric,Q1 2025,2025 Q2,Q3 2025\nRevenue,100,110,120\n", "quarterly"),
    ("metric,FY24,FY25,FY26\nRevenue,100,110,120\n", "yearly"),
])
def test_schema_warns_about_coarse_periods(client, text, kind):  # noqa: F811
    upload_id = _upload(client, text)
    response = client.get(f"/ingest/{upload_id}/schema")
    assert response.status_code == 200, response.text
    assert any(f"{kind} figures" in w and "export monthly figures" in w for w in response.json()["mapping_warnings"])


@pytest.mark.parametrize("layout", ["long", "wide"])
def test_total_opex_reported_and_keep_labels_restores_it(client, layout):  # noqa: F811
    if layout == "long":
        text = "period,metric,value\n2025-01,Revenue,100\n2025-01,Total Opex,60\n"
        mapping = dict(LONG)
    else:
        text = "metric,Jan-25\nRevenue,100\nTotal Opex,60\n"
        mapping = {"layout": "wide", "wide_metric_col": "metric", "wide_period_cols": ["Jan-25"]}
    upload_id = _upload(client, text)
    dropped = client.post(f"/ingest/{upload_id}/mapping", json=mapping).json()
    assert dropped["metrics"] == ["Revenue"]
    assert dropped["import_report"]["totals_dropped"] == [{"row": 2, "label": "Total Opex", "reason": "starts with Total"}]
    assert dropped["import_report"]["rows_kept"] == 1
    response = client.post(f"/ingest/{upload_id}/mapping", json={**mapping, "keep_labels": ["Total Opex"]})
    assert response.status_code == 200, response.text
    kept = response.json()
    assert kept["metrics"] == ["Revenue", "Total Opex"]
    assert kept["import_report"]["totals_dropped"] == []
    assert kept["import_report"]["rows_kept"] == 2
    assert {f["metric"]: f["value"] for f in client.get("/facts?period=2025-01").json()}["Total Opex"] == 60


def test_schema_total_preview_and_combination_warning_match_import(client):  # noqa: F811
    upload_id = _upload(client, "period,metric,value\n2025-01,Revenue,100\n2025-01,Total Opex,60\n2025-01,Revenue,200\n")
    schema = client.get(f"/ingest/{upload_id}/schema").json()
    assert schema["likely_total_rows"] == [{"row": 2, "label": "Total Opex", "reason": "starts with Total"}]
    assert any("2 rows" in w and "ADDED together" in w for w in schema["mapping_warnings"])
    result = client.post(f"/ingest/{upload_id}/mapping", json=schema["suggested_mapping"]).json()
    assert result["import_report"]["totals_dropped"] == schema["likely_total_rows"]


@pytest.mark.parametrize("keep_labels,expected_labels", [([], ["Subtotal Opex", "Grand total", "Hosting", "Rollup"]),
                                                       (["Hosting", "Rollup"], ["Subtotal Opex", "Grand total"])])
def test_all_total_rules_use_original_rows_and_allow_overrides(client, keep_labels, expected_labels):  # noqa: F811
    result = _import(client,
                     "period,metric,value,id,note\n2025-01,Revenue,100,A,detail\n,,,,\n"
                     "2025-01,Subtotal Opex,50,B,detail\n2025-01,Grand total,150,C,detail\n"
                     "2025-01,Hosting,20,,detail\n2025-01,Rollup,30,D,Total detail\n",
                     {**LONG, "id_col": "id", "keep_labels": keep_labels})
    report = result["import_report"]
    dropped = report["totals_dropped"]
    assert [r["label"] for r in dropped] == expected_labels
    assert dropped[:2] == [{"row": 3, "label": "Subtotal Opex", "reason": "starts with Total"},
                           {"row": 4, "label": "Grand total", "reason": "starts with Total"}]
    if not keep_labels:
        assert dropped[2:] == [{"row": 5, "label": "Hosting", "reason": "blank ID on a summary row"},
                               {"row": 6, "label": "Rollup", "reason": "starts with Total"}]


def test_total_only_file_can_be_previewed_then_kept(client):  # noqa: F811
    upload_id = _upload(client, "period,metric,value\n2025-01,Total Revenue,100\n")
    schema = client.get(f"/ingest/{upload_id}/schema").json()
    assert schema["likely_total_rows"] == [{"row": 1, "label": "Total Revenue", "reason": "starts with Total"}]
    response = client.post(f"/ingest/{upload_id}/mapping", json={**LONG, "keep_labels": ["Total Revenue"]})
    assert response.status_code == 200, response.text
    assert response.json()["metrics"] == ["Total Revenue"]
