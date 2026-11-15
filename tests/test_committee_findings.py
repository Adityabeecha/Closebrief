"""From the buyer-committee run: a quarterly file's year view says it's a year to date in
quarters, and a draft whose numbers failed the check is approved only with a reason."""

from test_datasets import client as client  # noqa: F401
from test_lab_findings import _import

from app.db import get_connection


def _report(ds, faithfulness):
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id = ? AND name = 'Revenue'", (ds,)).fetchone()["id"]
        conn.execute("""INSERT INTO generated_reports (metric_id, period, narrative, confidence, faithfulness)
                        VALUES (?, '2026-09', 'Revenue was $947.6K.', 'high', ?)""", (mid, faithfulness))
        conn.commit()
        return conn.execute("SELECT MAX(id) AS id FROM generated_reports").fetchone()["id"]
    finally:
        conn.close()


def test_a_quarterly_year_is_a_year_to_date_in_quarters(client):  # noqa: F811
    _import(client)
    years = {m["period"]: m for m in client.get("/close/months?granularity=year").json()}
    y = years["2026"]
    assert y["cadence"] == "quarter" and y["expected_count"] == 4 and y["month_count"] == 3 and y["partial"] is True
    steps = client.get("/close/status?period=2026").json()["steps"]
    assert "across 3 quarters" in steps[0]["detail"]
    assert "quarterly commentaries" in steps[1]["detail"] or steps[1]["state"] == "done"


def test_monthly_closes_are_untouched(client):  # noqa: F811
    _import(client)
    months = client.get("/close/months").json()
    assert months and all("partial" not in m for m in months)


def test_a_failed_check_needs_a_note_to_approve(client):  # noqa: F811
    ds = _import(client)
    bad = _report(ds, "failed")
    r = client.post(f"/reports/{bad}/review", json={"status": "approved"})
    assert r.status_code == 422 and "doesn't match the data" in r.json()["detail"]
    r = client.post(f"/reports/{bad}/review", json={"status": "approved", "note": "From the signed contract"})
    assert r.status_code == 200
    good = _report(ds, "passed")
    assert client.post(f"/reports/{good}/review", json={"status": "approved"}).status_code == 200


def test_security_page_is_public_and_honest(client):  # noqa: F811
    r = client.get("/security")
    assert r.status_code == 200
    for words in ("OpenAI", "row-level security", "No SOC 2", "No SAML SSO", "United States"):
        assert words in r.text
    assert 'href="/security"' in client.get("/privacy").text


def test_a_quarterly_close_is_titled_by_its_quarter(client):  # noqa: F811
    from app.board_pack import cadence_title
    ds = _import(client)
    client.patch(f"/datasets/{ds}", json={"amount_scale": 1})   # confirm units so the pack builds
    pack = client.get("/board-pack?period=2026-09").text
    assert "Q3 2026<br>Board Pack" in pack and "September 2026" not in pack
    conn = get_connection()
    try:
        assert cadence_title(conn, ds, "2026-09") == "Q3 2026"
        assert cadence_title(conn, None, "2026-09") == "September 2026"
    finally:
        conn.close()


def test_home_page_tour_video_is_served(client):  # noqa: F811
    home = client.get("/").text
    assert 'src="/media/closebrief-tour.mp4"' in home and "muted" in home
    video = client.get("/media/closebrief-tour.mp4")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4" and len(video.content) > 100_000
    assert client.get("/media/closebrief-tour.jpg").headers["content-type"] == "image/jpeg"
    assert client.get("/media/../app.py").status_code == 404
    assert client.get("/media/other.mp4").status_code == 404
