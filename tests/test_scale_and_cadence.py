"""Real filings: amounts "in thousands" are stored as real amounts, and a file of
quarter-end rows is a quarterly close (vs last quarter, year to date in quarters)."""

from test_datasets import client as client  # noqa: F401
from test_import_honesty import LONG, _upload

from app.compute.availability import reason_trigger
from app.compute.cadence import step_of
from app.db import get_connection

QUARTERLY = (
    "period,metric,department,actual,budget\n"
    "2025-03,Revenue,Consolidated,500,450\n2025-06,Revenue,Consolidated,520,470\n2025-09,Revenue,Consolidated,560,500\n"
    "2025-12,Revenue,Consolidated,600,520\n2026-03,Revenue,Consolidated,640,560\n2026-06,Revenue,Consolidated,700,580\n"
    "2025-03,Gross Margin %,Consolidated,75,76\n2025-06,Gross Margin %,Consolidated,74,76\n2025-09,Gross Margin %,Consolidated,74,76\n"
    "2025-12,Gross Margin %,Consolidated,73,76\n2026-03,Gross Margin %,Consolidated,72,76\n2026-06,Gross Margin %,Consolidated,71.8,73.1\n"
    "2025-03,Customers >$1M Product Revenue,Consolidated,600,600\n2025-06,Customers >$1M Product Revenue,Consolidated,650,650\n"
    "2025-09,Customers >$1M Product Revenue,Consolidated,690,690\n2025-12,Customers >$1M Product Revenue,Consolidated,730,700\n"
    "2026-03,Customers >$1M Product Revenue,Consolidated,780,720\n2026-06,Customers >$1M Product Revenue,Consolidated,828,750\n"
)
MAP = {**LONG, "value_col": "actual", "budget_col": "budget", "dimension_cols": ["department"]}


def _import(api, scale):
    uid = _upload(api, QUARTERLY)
    r = api.post(f"/ingest/{uid}/mapping", json={**MAP, "amount_scale": scale})
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


def _facts(api, period="2026-06"):
    return {f["metric"]: f for f in api.get(f"/facts?period={period}").json()}


def test_thousands_are_stored_as_real_amounts_and_rates_counts_are_not(client):  # noqa: F811
    ds = _import(client, 1000)
    f = _facts(client)
    assert f["Revenue"]["value"] == 700_000 and f["Revenue"]["budget"] == 580_000
    assert f["Gross Margin %"]["value"] == 71.8
    body = client.get("/datasets").json()
    assert (body.get("datasets", body) if isinstance(body, dict) else body)[0]["amount_scale"] == 1000
    conn = get_connection()
    try:
        member = conn.execute("""SELECT mb.value FROM metric_breakdowns mb JOIN metrics m ON m.id = mb.metric_id
                                 WHERE m.dataset_id = ? AND m.name = 'Revenue' AND mb.period = '2026-06'""", (ds,)).fetchone()
    finally:
        conn.close()
    assert member is None or member["value"] == 700_000


def test_changing_a_scaled_kpi_to_a_count_undoes_the_scaling(client):  # noqa: F811
    _import(client, 1000)
    name = "Customers >$1M Product Revenue"
    before = _facts(client)[name]["value"]
    r = client.post("/kpis", json={"kpis": [{"source_metric": name, "display_name": name, "category": "Growth",
                                             "unit": "count", "direction_good": "up", "aggregation_type": "balance"}]})
    assert r.status_code == 200, r.text
    after = _facts(client)[name]["value"]
    assert after == 828 and before in (828, 828_000)   # library may already call it a count


def test_a_quarterly_file_compares_with_the_last_quarter(client):  # noqa: F811
    _import(client, 1)
    f = _facts(client)["Revenue"]
    assert f["cadence"] == "quarter"
    assert f["prior_value"] == 640 and round(f["deltas"]["mom_pct"], 2) == 9.38
    assert f["ytd"]["months"] == 2 and f["ytd"]["step"] == 3
    assert f["ytd"]["budget"] == 1140                 # both quarters have a plan


def test_cadence_detection():
    assert step_of([0, 3, 6, 9]) == 3
    assert step_of([0, 1, 2]) == 1
    assert step_of([0, 12, 24]) == 12
    assert step_of([0, 12]) == 1                      # two points aren't enough


def test_an_unplanned_part_asks_for_a_reason_when_material():
    big = {"member": "Restructuring", "var_abs": 150_693, "var_pct": 36.5, "unplanned": True}
    small = {"member": "Restructuring", "var_abs": 996, "var_pct": 0.17, "unplanned": True}
    t = reason_trigger(False, 3.0, 10, budget_var_abs=1000, parts=[big])
    assert t["kind"] == "part" and t["unplanned"] is True
    assert reason_trigger(False, 3.0, 10, budget_var_abs=1000, parts=[small]) is None


def test_amount_scale_must_be_a_known_factor(client):  # noqa: F811
    uid = _upload(client, QUARTERLY)
    assert client.post(f"/ingest/{uid}/mapping", json={**MAP, "amount_scale": 7}).status_code == 422
