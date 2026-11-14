"""Reasons an analyst doesn't have to retype: a comment column in the import, a pasted
document split into notes, and notes that cover several periods or recur."""

import json

from test_datasets import client as client  # noqa: F401
from test_import_honesty import LONG, _upload

from app.context.extract import parse_proposals
from app.context.reasons import reason_recorded
from app.schemas import ContextDoc

FILE = ("period,metric,actual,budget,comment\n"
        "2026-08,Revenue,100,100,\n2026-09,Revenue,100,100,\n"
        "2026-10,Revenue,80,100,Two EMEA renewals slipped to November\n"
        "2026-10,Opex,70,50,Dreamforce booth and travel\n2026-09,Opex,50,50,\n2026-08,Opex,50,50,\n")


def test_a_comment_column_becomes_notes_for_its_kpi_and_month(client):  # noqa: F811
    uid = _upload(client, FILE)
    r = client.post(f"/ingest/{uid}/mapping", json={**LONG, "value_col": "actual", "budget_col": "budget",
                                                     "comment_col": "comment"})
    assert r.status_code == 200, r.text
    assert r.json()["notes_added"] == 2
    notes = {n["metric_tags"][0]: n for n in client.get("/context").json()}
    assert notes["Revenue"]["effective_date"] == "2026-10" and "EMEA" in notes["Revenue"]["body"]
    facts = {f["metric"]: f for f in client.get("/facts?period=2026-10").json()}
    assert facts["Revenue"]["reason_note"] and facts["Opex"]["reason_note"]
    # Re-importing the same file replaces those notes rather than doubling them.
    uid2 = _upload(client, FILE)
    client.post(f"/ingest/{uid2}/mapping", json={**LONG, "value_col": "actual", "budget_col": "budget",
                                                  "comment_col": "comment"}, params={"target_dataset_id": r.json()["dataset_id"]})
    assert len(client.get("/context").json()) == 2


def _doc(**kw):
    return ContextDoc(**{"id": 1, "type": "event_note", "title": "t", "body": "b",
                         "metric_tags": ["Operating expenses"], **kw})


def test_a_recurring_note_counts_every_period_until_turned_off():
    ongoing = _doc(effective_date="2026-03", effective_until="ongoing")
    assert reason_recorded(ongoing, "Operating expenses", "2026-09")
    assert not reason_recorded(ongoing, "Operating expenses", "2026-01")
    ranged = _doc(effective_date="2026-06", effective_until="2026-09")
    assert reason_recorded(ranged, "Operating expenses", "2026-06") and reason_recorded(ranged, "Operating expenses", "2026-09")
    assert not reason_recorded(ranged, "Operating expenses", "2026-12")


def test_effective_until_is_validated(client):  # noqa: F811
    bad = client.post("/context", json={"title": "x", "body": "y", "effective_until": "soon"})
    assert bad.status_code == 422
    ok = client.post("/context", json={"title": "x", "body": "y", "metric_tags": ["Revenue"],
                                       "effective_date": "2026-01", "effective_until": "ongoing"})
    assert ok.status_code == 201 and ok.json()["effective_until"] == "ongoing"


def test_pasted_document_proposals_keep_only_known_kpis():
    raw = "```json\n" + json.dumps([
        {"kpi": "operating expenses", "title": "Restructuring", "body": "$150,693 thousand restructuring.", "period": "2026-06"},
        {"kpi": "Made-up KPI", "title": "x", "body": "y", "period": "2026-06"},
        {"kpi": "Revenue", "title": "Workers AI", "body": "Enterprise adoption of Workers.", "period": None, "recurring": False},
        {"kpi": "Revenue", "title": "dup", "body": "again", "period": "2026-06"},
        {"kpi": "Cost of revenue", "title": "SBC", "body": "", "period": "2026-06"},
    ]) + "\n```"
    out = parse_proposals(raw, ["Operating expenses", "Revenue", "Cost of revenue"], "2026-06")
    assert [(p["kpi"], p["period"]) for p in out] == [("Operating expenses", "2026-06"), ("Revenue", "2026-06")]
    assert parse_proposals("not json", ["Revenue"], "2026-06") == []


def test_extract_needs_text(client):  # noqa: F811
    assert client.post("/context/extract", json={"text": "short"}).status_code == 422
