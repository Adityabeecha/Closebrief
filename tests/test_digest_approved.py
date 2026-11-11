"""P1: the digest is built only from approved commentary, and sending it is a
separate step refused until every KPI in the close is approved. P4: only
analysts and admins approve. P5: approval resets when a re-import changes a figure."""

import pytest
from test_auth import _bearer, make_client  # noqa: F401  (make_client is a pytest fixture)

import app.routers.generate as route_generate


def _seed(approve_all: bool):
    from app.datasets import create_dataset
    from app.db import get_connection

    conn = get_connection()
    ds = create_dataset(conn, "Digest test")
    rows = [("Revenue", -370000.0, "Revenue fell to $4.2M, 8.1% below plan. Pricing churned legacy accounts."),
            ("Cost", 50000.0, "Cost rose to $1.1M. No reason recorded yet."),
            ("Cash", None, "Cash was $9.0M.")]
    ids = {}
    for name, bud, text in rows:
        mid = conn.execute("INSERT INTO metrics (dataset_id, name) VALUES (?, ?)", (ds, name)).lastrowid
        ids[name] = mid
        conn.execute("INSERT INTO computed_facts (metric_id, period, value, budget_var_abs) VALUES (?, '2026-06', 1, ?)",
                     (mid, bud))
        status = "approved" if (approve_all or name == "Revenue") else None
        conn.execute(
            "INSERT INTO generated_reports (metric_id, period, narrative, confidence, faithfulness, review_status) "
            "VALUES (?, '2026-06', ?, 'High', 'passed', ?)", (mid, text, status))
    # A newer unapproved draft for Revenue would hide the approved one: the latest draft counts.
    conn.commit()
    conn.close()
    return ds, ids


def test_digest_contains_only_approved_commentary(make_client):  # noqa: F811
    client = make_client(auth_on=False)
    _seed(approve_all=False)
    d = client.get("/digest?period=2026-06").json()
    assert [it["metric"] for it in d["items"]] == ["Revenue"]
    assert d["items"][0]["headline"] == "Revenue fell to $4.2M, 8.1% below plan."
    assert d["items"][0]["detail"] == "Pricing churned legacy accounts."
    assert (d["total_kpis"], d["approved_kpis"], d["ready"]) == (3, 1, False)
    assert sorted(d["pending"]) == ["Cash", "Cost"]


def test_send_is_refused_until_everything_is_approved(make_client, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(route_generate, "_notify_async", lambda *a, **k: sent.append((a, k)))
    client = make_client(auth_on=False)
    _seed(approve_all=False)
    r = client.post("/digest/send?period=2026-06")
    assert r.status_code == 409 and "2 KPIs still need approval" in r.json()["detail"]
    assert sent == []
    assert client.get("/digests/history").json() == []


def test_send_records_the_run_and_notifies_once_ready(make_client, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(route_generate, "_notify_async", lambda *a, **k: sent.append((a, k)))
    client = make_client(auth_on=False)
    _seed(approve_all=True)
    r = client.post("/digest/send?period=2026-06")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready"] and body["sent_at"]
    # Ranked by absolute variance against plan: Revenue (370K) before Cost (50K) before Cash (none).
    assert [it["metric"] for it in body["items"]] == ["Revenue", "Cost", "Cash"]
    assert len(sent) == 1 and sent[0][0][0] == "digest_generated"
    runs = client.get("/digests/history").json()
    assert len(runs) == 1 and runs[0]["trigger"] == "sent"
    assert client.get("/digest?period=2026-06").json()["sent_at"]


# Postgres stores user ids as UUIDs.
EXEC_ID = "00000000-0000-4000-8000-0000000000e1"


def test_executives_cannot_approve_or_send(make_client):  # noqa: F811
    client = make_client(auth_on=True, seed_roles={EXEC_ID: ("e@co.com", "executive")})
    h = _bearer(sub=EXEC_ID, email="e@co.com")
    assert client.post("/reports/1/review", json={"status": "approved"}, headers=h).status_code == 403
    assert client.post("/digest/send?period=2026-06", headers=h).status_code == 403


def test_reimport_with_changed_figures_resets_approval(make_client):  # noqa: F811
    make_client(auth_on=False)
    from app.compute.kpis import compute_and_store
    from app.datasets import create_dataset
    from app.db import get_connection

    conn = get_connection()
    ds = create_dataset(conn, "Reset test")
    a = conn.execute("INSERT INTO metrics (dataset_id, name) VALUES (?, 'Revenue')", (ds,)).lastrowid
    b = conn.execute("INSERT INTO metrics (dataset_id, name) VALUES (?, 'Cost')", (ds,)).lastrowid
    for mid in (a, b):
        for period, v in (("2026-05", 100.0), ("2026-06", 110.0)):
            conn.execute("INSERT INTO metric_values (metric_id, period, value) VALUES (?, ?, ?)", (mid, period, v))
    conn.commit()
    compute_and_store(conn, ds)
    for mid in (a, b):
        conn.execute("INSERT INTO generated_reports (metric_id, period, narrative, confidence, faithfulness, review_status) "
                     "VALUES (?, '2026-06', 'x', 'High', 'passed', 'approved')", (mid,))
    conn.commit()
    # A corrected file changes Revenue's June figure; Cost is untouched.
    conn.execute("UPDATE metric_values SET value = 125 WHERE metric_id = ? AND period = '2026-06'", (a,))
    conn.commit()
    compute_and_store(conn, ds)
    got = {r["metric_id"]: (r["review_status"], r["faithfulness"]) for r in conn.execute(
        "SELECT metric_id, review_status, faithfulness FROM generated_reports").fetchall()}
    conn.close()
    assert got[a] == (None, "unchecked")
    assert got[b] == ("approved", "passed")


@pytest.mark.parametrize("narrative,headline,detail", [
    ("One sentence only.", "One sentence only.", ""),
    ("Revenue was $4.2M. Up 3.1% vs plan.", "Revenue was $4.2M.", "Up 3.1% vs plan."),
    ("Margin hit 38.6%. −1.2 pts vs plan.", "Margin hit 38.6%.", "−1.2 pts vs plan."),
])
def test_headline_is_the_first_sentence(narrative, headline, detail):
    from app.digest.digest import _split_headline
    assert _split_headline(narrative) == (headline, detail)
