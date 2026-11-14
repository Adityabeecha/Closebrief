"""A manager approves a close from a signed link, without an account."""

import json

import pytest
from test_datasets import client as client  # noqa: F401
from test_import_honesty import LONG, _upload

from app.config import settings
from app.db import get_connection

FILE = "period,metric,value,budget\n2026-08,Revenue,100,100\n2026-09,Revenue,80,100\n"


@pytest.fixture
def seeded(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "session_jwt_secret", "test-secret-for-approval-links-0123456789")
    client.post(f"/ingest/{_upload(client, FILE)}/mapping", json={**LONG, "budget_col": "budget"})
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE name = 'Revenue'").fetchone()["id"]
        conn.execute("""INSERT INTO generated_reports (metric_id, period, narrative, sources, confidence, faithfulness,
                        prompt_tokens, completion_tokens, cost_usd, latency_ms, prompt_version)
                        VALUES (?, '2026-09', 'Revenue was $80, $20 below plan.', ?, 'High', 'passed', 0, 0, 0, 0, 'v6')""",
                     (mid, json.dumps([])))
        conn.commit()
    finally:
        conn.close()
    return client


def test_link_page_and_approval(seeded):
    r = seeded.post("/close/approval-link", json={"period": "2026-09"})
    assert r.status_code == 200, r.text
    token = r.json()["url"].split("t=", 1)[1]
    page = seeded.get(f"/approve?t={token}")
    assert page.status_code == 200 and "Revenue was $80" in page.text and "noindex" in page.text
    assert seeded.post("/approve", json={"t": token, "metric": "Revenue", "action": "approved"}).status_code == 422  # no name
    ok = seeded.post("/approve", json={"t": token, "name": "Priya", "metric": "Revenue", "action": "approved"})
    assert ok.status_code == 200 and ok.json()["review_status"] == "approved"
    facts = {f["metric"]: f for f in seeded.get("/facts?period=2026-09").json()}
    assert facts["Revenue"]["review_status"] == "approved"


def test_a_bad_or_other_period_link_is_refused(seeded):
    assert seeded.get("/approve?t=not-a-token").status_code == 404
    token = seeded.post("/close/approval-link", json={"period": "2026-08"}).json()["url"].split("t=", 1)[1]
    r = seeded.post("/approve", json={"t": token, "name": "Priya", "metric": "Revenue", "action": "approved"})
    assert r.status_code == 404   # no draft in that close
