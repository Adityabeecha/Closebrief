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


def test_board_pack_warns_when_a_thousands_dataset_is_not_scaled(client):  # noqa: F811
    ds = _import(client)
    client.patch(f"/datasets/{ds}", json={"name": "HubSpot (SEC, $ thousands)"})
    assert "Check the units before sharing" in client.get("/board-pack?period=2026-09").text
    client.patch(f"/datasets/{ds}", json={"amount_scale": 1000})
    assert "Check the units before sharing" not in client.get("/board-pack?period=2026-09").text
