"""U7: an approval made by mistake (e.g. a bulk approve) can be undone by
reopening the draft as unreviewed."""

from test_auth import make_client  # noqa: F401  (make_client is a pytest fixture)
from test_digest_approved import _seed


def _status(report_id):
    from app.db import get_connection
    conn = get_connection()
    try:
        return conn.execute("SELECT review_status FROM generated_reports WHERE id = ?",
                            (report_id,)).fetchone()["review_status"]
    finally:
        conn.close()


def _report_for(metric_id):
    from app.db import get_connection
    conn = get_connection()
    try:
        return conn.execute("SELECT id FROM generated_reports WHERE metric_id = ?", (metric_id,)).fetchone()["id"]
    finally:
        conn.close()


def test_draft_reopens_an_approved_report_as_unreviewed(make_client):  # noqa: F811
    client = make_client(auth_on=False)
    _, ids = _seed(approve_all=False)
    rid = _report_for(ids["Cost"])
    assert client.post(f"/reports/{rid}/review", json={"status": "approved"}).status_code == 200
    r = client.post(f"/reports/{rid}/review", json={"status": "draft"})
    assert r.status_code == 200 and r.json()["review_status"] is None
    assert _status(rid) is None
    # Reopened, it counts as waiting again in the digest.
    assert "Cost" in client.get("/digest?period=2026-06").json()["pending"]


def test_undo_can_restore_an_earlier_decision(make_client):  # noqa: F811
    client = make_client(auth_on=False)
    _, ids = _seed(approve_all=False)
    rid = _report_for(ids["Cost"])
    client.post(f"/reports/{rid}/review", json={"status": "pending"})
    client.post(f"/reports/{rid}/review", json={"status": "approved"})
    assert client.post(f"/reports/{rid}/review", json={"status": "pending"}).status_code == 200
    assert _status(rid) == "pending"


def test_unknown_status_is_still_refused(make_client):  # noqa: F811
    client = make_client(auth_on=False)
    _, ids = _seed(approve_all=False)
    rid = _report_for(ids["Cost"])
    assert client.post(f"/reports/{rid}/review", json={"status": "shipped"}).status_code == 422
