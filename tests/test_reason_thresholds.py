"""A KPI needs a reason when it, or any one part of it, misses plan past the dataset's
threshold, and (if set) by at least the dollar floor; only a note about that KPI and
month counts as its reason."""

import pytest
from test_datasets import client as client
from test_import_honesty import LONG, _import

from app.compute.availability import reason_trigger
from app.routers.close import reason_recorded
from app.schemas import ContextDoc

SALES = {"member": "Sales", "var_pct": 12.8, "var_abs": 61319.44}
RND = {"member": "R&D", "var_pct": -3.8, "var_abs": -23323.26}


def test_a_part_past_the_threshold_flags_a_quiet_total():
    t = reason_trigger(False, 3.1, 10, budget_var_abs=42670, parts=[SALES, RND])
    assert t == {"kind": "part", "part": "Sales", "var_pct": 12.8, "var_abs": 61319.44}
    assert reason_trigger(False, 3.1, 10, budget_var_abs=42670, parts=[RND]) is None
    assert reason_trigger(True, 1, 10)["kind"] == "anomaly"
    assert reason_trigger(False, 12, 10, budget_var_abs=500)["kind"] == "total"


def test_the_dollar_floor_must_also_be_passed():
    assert reason_trigger(False, 12, 10, budget_var_abs=1200, floor=25000) is None
    assert reason_trigger(False, 3.1, 10, budget_var_abs=42670, floor=100000, parts=[SALES]) is None
    assert reason_trigger(False, 3.1, 10, budget_var_abs=42670, floor=50000, parts=[SALES])["part"] == "Sales"


def _doc(**kw):
    return ContextDoc(**{"id": 1, "type": "event_note", "title": "t", "body": "b", "metric_tags": [], **kw})


def test_only_a_note_about_that_kpi_and_month_counts():
    assert not reason_recorded(_doc(title="Q1 seasonality"), "Operating Expenses", "2026-03")
    assert not reason_recorded(_doc(metric_tags=["Operating Expenses"], effective_date="2025-03"),
                               "Operating Expenses", "2026-03")
    assert not reason_recorded(_doc(type="definition", metric_tags=["Operating Expenses"]), "Operating Expenses", "2026-03")
    assert reason_recorded(_doc(metric_tags=["operating expenses"], effective_date="2026-03"), "Operating Expenses", "2026-03")
    assert reason_recorded(_doc(metric_tags=["Operating Expenses"]), "Operating Expenses", "2026-03")


def test_an_untagged_note_no_longer_ticks_off_the_checklist(client):
    _import(client, "period,metric,value,budget\n2025-01,Revenue,100,100\n2025-02,Revenue,130,100\n",
            {**LONG, "budget_col": "budget"})
    client.post("/context", json={"type": "event_note", "title": "Q1 seasonality", "body": "Seasonal.", "metric_tags": []})
    month = next(m for m in client.get("/close/months").json() if m["period"] == "2025-02")
    assert month["needs_reason"] == 1


@pytest.mark.parametrize("bad", [-1, "5000", True])
def test_dollar_floor_validation(client, bad):
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n")["dataset_id"]
    assert client.patch(f"/datasets/{ds}", json={"reason_threshold_abs": bad}).status_code == 422
    ok = client.patch(f"/datasets/{ds}", json={"reason_threshold_abs": None})
    assert ok.status_code == 200 and ok.json()["reason_threshold_abs"] is None
