"""Fixes from the analyst's end-to-end run on the live site: notes belong to one dataset,
only a tagged note explains a KPI, an edited draft is re-checked, and a quarter rolls up
the state of its months."""

import json

from test_datasets import client as client  # noqa: F401
from test_import_honesty import LONG, _import

from app.db import get_connection
from app.generation.guard import figures_not_in

PL = ("period,metric,value,budget\n"
      "2026-08,Revenue,100,100\n2026-09,Revenue,100,100\n2026-10,Revenue,80,100\n"
      "2026-08,Sales & Marketing,50,50\n2026-09,Sales & Marketing,50,50\n2026-10,Sales & Marketing,70,50\n")
BUDGET = {**LONG, "budget_col": "budget"}


def _two_datasets(api):
    a = _import(api, PL, BUDGET)["dataset_id"]
    b = _import(api, PL, BUDGET)["dataset_id"]
    return a, b


def _note(api, **kw):
    body = {"type": "event_note", "title": "Why Revenue moved", "body": "Two renewals slipped.",
            "metric_tags": ["Revenue"], "effective_date": "2026-10", **kw}
    r = api.post("/context", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _needs(api, period="2026-10"):
    return next(m for m in api.get("/close/months").json() if m["period"] == period)["needs_reason"]


def test_a_note_explains_only_its_own_dataset(client):  # noqa: F811
    a, b = _two_datasets(client)
    client.post(f"/datasets/{a}/activate")
    note = _note(client)
    assert note["dataset_id"] == a and note["scope"] == "dataset"
    assert _needs(client) == 1                       # S&M still open in A; Revenue explained
    client.post(f"/datasets/{b}/activate")
    assert _needs(client) == 2                       # B's Revenue isn't explained by A's note
    assert note["id"] not in [d["id"] for d in client.get("/context").json()]
    facts = {f["metric"]: f for f in client.get("/facts?period=2026-10").json()}
    assert facts["Revenue"]["reason_note"] is None
    client.post(f"/datasets/{a}/activate")
    facts = {f["metric"]: f for f in client.get("/facts?period=2026-10").json()}
    assert facts["Revenue"]["reason_note"]["id"] == note["id"]


def test_a_workspace_note_applies_everywhere_and_a_dataset_takes_its_notes_with_it(client):  # noqa: F811
    a, b = _two_datasets(client)
    client.post(f"/datasets/{a}/activate")
    shared = _note(client, scope="workspace", title="Group policy", type="policy", metric_tags=[])
    own = _note(client)
    assert shared["dataset_id"] is None
    client.post(f"/datasets/{b}/activate")
    assert shared["id"] in [d["id"] for d in client.get("/context").json()]
    assert client.delete(f"/datasets/{a}").status_code in (200, 204)
    conn = get_connection()
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM context_documents").fetchall()]
    finally:
        conn.close()
    assert own["id"] not in ids and shared["id"] in ids


def test_conflicts_only_between_notes_about_this_datasets_kpis(client):  # noqa: F811
    _import(client, PL, BUDGET)
    for title, fig in (("March pricing memo", "$612K"), ("Q1 churn analysis", "$580K")):
        _note(client, scope="workspace", title=title, metric_tags=["Churned ARR"], effective_date="2025-03",
              body=f"Churned ARR from legacy enterprise accounts after pricing was {fig} monthly")
    assert client.get("/context/conflicts").json() == []


def test_an_edit_with_a_new_figure_is_flagged_and_needs_approving_again(client):  # noqa: F811
    _import(client, PL, BUDGET)
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE name = 'Revenue'").fetchone()["id"]
        cur = conn.execute(
            """INSERT INTO generated_reports (metric_id, period, narrative, sources, confidence, faithfulness,
               prompt_tokens, completion_tokens, cost_usd, latency_ms, prompt_version, review_status)
               VALUES (?, '2026-10', 'Revenue was $80, $20 below plan.', ?, 'High', 'passed', 0, 0, 0, 0, 'v5', 'approved')""",
            (mid, json.dumps([])))
        conn.commit()
        rid = cur.lastrowid
    finally:
        conn.close()
    ok = client.post("/feedback", json={"report_id": rid, "action": "edited",
                                        "edited_text": "Revenue came in at $80, short of plan by $20."})
    assert ok.status_code == 201 and ok.json()["unverified"] == []
    bad = client.post("/feedback", json={"report_id": rid, "action": "edited",
                                         "edited_text": "Revenue was $80, $35 below plan."})
    assert bad.json()["unverified"] == [35.0]
    conn = get_connection()
    try:
        row = conn.execute("SELECT faithfulness, review_status FROM generated_reports WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()
    assert row["faithfulness"] == "failed" and row["review_status"] is None


def test_figures_not_in_allows_rewording_and_rounding():
    assert figures_not_in("Revenue $3.40M, −8.4% vs plan.", ["Revenue was $3,399,611 (-8.4%)."]) == []
    assert figures_not_in("Up $500K.", ["Up $169K."]) == [500000.0]


def test_a_quarter_is_locked_when_its_months_with_figures_are(client):  # noqa: F811
    ds = _import(client, PL, BUDGET)["dataset_id"]
    conn = get_connection()
    try:   # locking needs a sent digest; the roll-up is what's under test
        conn.execute("""INSERT INTO close_locks (workspace_id, dataset_id, period, locked_at, locked_by_email)
                        VALUES (NULL, ?, '2026-10', CURRENT_TIMESTAMP, 'a@b.c')""", (ds,))
        conn.commit()
    finally:
        conn.close()
    q4 = next(m for m in client.get("/close/months?granularity=quarter").json() if m["period"] == "2026-Q4")
    assert q4["locked"] is True
    q3 = next(m for m in client.get("/close/months?granularity=quarter").json() if m["period"] == "2026-Q3")
    assert q3["locked"] is False
