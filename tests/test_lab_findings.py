"""From the panel run on the live site: a '$ thousands' dataset imported before the
option existed can be fixed in place, and quarter/year board packs aren't empty."""

import json

from test_datasets import client as client  # noqa: F401
from test_import_honesty import LONG, _upload

from app.db import get_connection

FILE = ("period,metric,actual,budget\n"
        "2026-03,Revenue,862264,698728\n2026-06,Revenue,894025,744532\n2026-09,Revenue,947565,809518\n"
        "2026-03,Customers,297576,258258\n2026-06,Customers,306446,267982\n2026-09,Customers,311704,278880\n")
MAP = {**LONG, "value_col": "actual", "budget_col": "budget"}


def _import(api):
    r = api.post(f"/ingest/{_upload(api, FILE)}/mapping", json=MAP)
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


def _facts(api, period="2026-09", granularity="month"):
    return {f["metric"]: f for f in api.get(f"/facts?period={period}&granularity={granularity}").json()}


def test_an_existing_dataset_switches_to_thousands_in_place(client):  # noqa: F811
    ds = _import(client)
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE name = 'Revenue'").fetchone()["id"]
        conn.execute("""INSERT INTO generated_reports (metric_id, period, narrative, sources, confidence, faithfulness,
                        prompt_tokens, completion_tokens, cost_usd, latency_ms, prompt_version, review_status)
                        VALUES (?, '2026-09', 'Revenue was $948K.', ?, 'High', 'passed', 0, 0, 0, 0, 'v8', 'approved')""",
                     (mid, json.dumps([])))
        conn.commit()
    finally:
        conn.close()
    assert _facts(client)["Revenue"]["value"] == 947565
    assert client.patch(f"/datasets/{ds}", json={"amount_scale": 7}).status_code == 422
    assert client.patch(f"/datasets/{ds}", json={"amount_scale": 1000}).status_code == 200
    f = _facts(client)
    assert f["Revenue"]["value"] == 947_565_000 and f["Revenue"]["budget"] == 809_518_000
    assert f["Customers"]["value"] == 311704                       # a count isn't scaled
    assert f["Revenue"]["faithfulness"] != "passed" and f["Revenue"]["review_status"] is None   # "$948K" is stale
    assert client.patch(f"/datasets/{ds}", json={"amount_scale": 1}).status_code == 200
    assert _facts(client)["Revenue"]["value"] == 947565            # and back


def test_quarter_and_year_board_packs_have_the_kpis(client):  # noqa: F811
    _import(client)
    for period in ("2026-Q3", "2026"):
        r = client.get(f"/board-pack?period={period}")
        assert r.status_code == 200
        assert "No KPIs with data for this period" not in r.text and "Revenue" in r.text, period


def test_unconfirmed_units_block_sharing_until_someone_confirms(client):  # noqa: F811
    ds = _import(client)
    client.patch(f"/datasets/{ds}", json={"name": "HubSpot (SEC, $ thousands)"})
    blocked = client.get("/board-pack?period=2026-09")
    assert blocked.status_code == 409 and "Confirm the units first" in blocked.json()["detail"]
    assert client.get("/close/export.xlsx?period=2026-09").status_code == 409
    assert client.post("/close/approval-link", json={"period": "2026-09"}).status_code in (409, 503)
    conn = get_connection()
    try:
        rid = conn.execute("""SELECT gr.id FROM generated_reports gr JOIN metrics m ON m.id = gr.metric_id
                              WHERE m.dataset_id = ? LIMIT 1""", (ds,)).fetchone()
    finally:
        conn.close()
    if rid:
        assert client.post(f"/reports/{rid['id']}/review", json={"status": "approved"}).status_code == 409
    # Keeping "units" is a valid answer too: choosing it is the confirmation, and it's logged.
    client.patch(f"/datasets/{ds}", json={"amount_scale": 1})
    ok = client.get("/board-pack?period=2026-09")
    assert ok.status_code == 200 and "Check the units" not in ok.text
    body = client.get("/datasets").json()
    row = next(d for d in (body.get("datasets", body) if isinstance(body, dict) else body) if d["id"] == ds)
    assert row["units_confirmed"] is True
    conn = get_connection()
    try:
        logged = conn.execute("SELECT summary FROM audit_log WHERE action = 'units_confirmed' AND entity_id = ?",
                              (str(ds),)).fetchall() or conn.execute(
            "SELECT summary FROM audit_log WHERE action = 'units_confirmed'").fetchall()
    finally:
        conn.close()
    assert logged


def test_a_plain_dataset_is_never_blocked(client):  # noqa: F811
    _import(client)
    assert client.get("/board-pack?period=2026-09").status_code == 200
