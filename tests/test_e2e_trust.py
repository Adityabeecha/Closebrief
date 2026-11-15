"""From the end-to-end tester: a locked close keeps its notes, quarter dates mean a
quarter, and the commentary check catches what the tester slipped past it."""

import pytest
from pydantic import ValidationError
from test_datasets import client as client  # noqa: F401
from test_lab_findings import _import

from app.db import get_connection
from app.schemas import ContextDocIn


def _lock(ds, period, on=True):
    conn = get_connection()
    try:
        if on:
            conn.execute("INSERT INTO close_locks (dataset_id, period) VALUES (?, ?)", (ds, period))
        else:
            conn.execute("DELETE FROM close_locks WHERE dataset_id = ? AND period = ?", (ds, period))
        conn.commit()
    finally:
        conn.close()


def _note(api, **kw):
    body = {"type": "event_note", "title": "Why", "body": "Because", "metric_tags": ["Revenue"], **kw}
    return api.post("/context", json=body)


def test_quarter_and_year_dates_become_month_ranges():
    q = ContextDocIn(title="t", body="b", effective_date="2026-Q3")
    assert (q.effective_date, q.effective_until) == ("2026-07", "2026-09")
    assert ContextDocIn(title="t", body="b", effective_date="Q1 2026").effective_until == "2026-03"
    y = ContextDocIn(title="t", body="b", effective_date="FY2026")
    assert (y.effective_date, y.effective_until) == ("2026-01", "2026-12")
    assert ContextDocIn(title="t", body="b", effective_date="2026-09").effective_until is None
    with pytest.raises(ValidationError):
        ContextDocIn(title="t", body="b", effective_date="next quarter")


def test_a_locked_close_keeps_its_notes(client):  # noqa: F811
    ds = _import(client)
    before = _note(client, effective_date="2026-09")
    assert before.status_code == 201, before.text
    _lock(ds, "2026-09")

    blocked = _note(client, effective_date="2026-09")
    assert blocked.status_code == 409 and "locked close" in blocked.json()["detail"]
    assert _note(client, effective_date="2026-Q3").status_code == 409          # Jul-Sep covers September
    assert _note(client, effective_date="2026-07").status_code == 409          # an event note runs 2 months on
    assert _note(client, effective_date="2026-03", type="definition").status_code == 201
    assert _note(client).status_code == 201                                    # undated: every close, not this one's record
    doc = before.json()["id"]
    assert client.delete(f"/context/{doc}").status_code == 409
    assert client.put(f"/context/{doc}", json={"type": "event_note", "title": "Changed", "body": "x",
                                                "effective_date": "2026-09"}).status_code == 409

    _lock(ds, "2026-09", on=False)
    assert client.delete(f"/context/{doc}").status_code == 204


def test_an_edit_that_turns_a_number_around_is_flagged(client):  # noqa: F811
    from test_committee_findings import _report
    ds = _import(client)
    rid = _report(ds, "passed")   # Revenue Q3: 947,565 vs plan 809,518 = +17.05%
    ok = client.post("/feedback", json={"report_id": rid, "action": "edited",
                                        "edited_text": "Revenue was $947.6K, ahead of plan by 17.1%."}).json()
    assert ok["direction"] == []
    bad = client.post("/feedback", json={"report_id": rid, "action": "edited",
                                         "edited_text": "Revenue was $947.6K, falling below plan by 17.1%."}).json()
    assert bad["direction"] and "17.1%" in bad["direction"][0]
    conn = get_connection()
    try:
        assert conn.execute("SELECT faithfulness FROM generated_reports WHERE id = ?", (rid,)).fetchone()[0] == "failed"
    finally:
        conn.close()


def test_a_quarterly_forecast_steps_by_quarter(client):  # noqa: F811
    from app.compute.forecast import next_periods
    assert next_periods("2026-09", 3, 3) == ["2026-12", "2027-03", "2027-06"]
    assert next_periods("2026-11", 2) == ["2026-12", "2027-01"]
    _import(client)
    fc = client.get("/forecast?metric=Revenue&horizon=2").json()
    assert fc["cadence"] == "quarter" and [p["period"] for p in fc["projections"]] == ["2026-12", "2027-03"]


def test_the_kpi_name_is_not_a_figure_and_rate_changes_are_points():
    from app.generation.guard import check_faithfulness
    from app.grounding.attribution import attribute
    from app.schemas import ComputedFact, Deltas
    c = ComputedFact(metric="Customers >$100K ARR", period="2026-09", value=4860, prior_value=4720, unit="count",
                     deltas=Deltas(mom_pct=2.966, yoy_pct=19.704, budget_var_abs=800, budget_var_pct=19.704))
    text = "Customers >$100K ARR reached 4,860, exceeding plan by 800."
    assert check_faithfulness(text, c) == (True, [])
    assert [s["kind"] for s in attribute(text, c)["sentences"]] == ["verified"]
    assert check_faithfulness("Customers >$100K ARR reached 5,200.", c)[0] is False   # a wrong count still fails
    g = ComputedFact(metric="GAAP Gross Margin %", period="2026-09", value=80, prior_value=79, unit="%",
                     deltas=Deltas(mom_pct=1.2658, yoy_pct=-0.1248, budget_var_abs=-1, budget_var_pct=-1.2346))
    ok = "GAAP Gross Margin % was 80.0%, 1.0 point below plan. It rose 1.0 point from last quarter."
    assert check_faithfulness(ok, g) == (True, [])
    assert check_faithfulness("GAAP Gross Margin % was 80.0%, 1.0 point above plan, and fell 1.0 point.", g)[0] is False


def test_board_pack_top_mover_is_the_largest_percent_gap():
    from app.board_pack import build_board_pack_html

    def f(metric, unit, var_abs, var_pct):
        return {"metric": metric, "unit": unit, "value": 1, "has_data": True,
                "deltas": {"budget_var_abs": var_abs, "budget_var_pct": var_pct}}
    doc = build_board_pack_html([f("Revenue", "USD", 311_440_000, 35.2), f("Operating expenses", "USD", 247_740_000, 38.9),
                                 f("Gross margin %", "%", -1.0, -60.0)], "2026-09")
    assert "Operating expenses +38.9% vs plan" in doc and "largest % gap to plan" in doc
