"""F14: anomalies judged against the same month last year when there's history, with
the basis and z exposed; and a per-dataset 'needs a reason' threshold."""

import pytest
from test_datasets import client as client
from test_import_honesty import LONG, _import

from app.compute.kpis import judge_anomalies


def _series(years, december=1.4, last_december=None):
    """Monthly values, flat with small wiggles, and a December spike every year."""
    rows, wiggle = [], [1.0, 1.01, 0.99, 1.02, 0.98, 1.0, 1.01, 0.99, 1.0, 1.02, 0.99]
    for y in range(years):
        for m in range(1, 13):
            v = 100.0 * (wiggle[m - 1] if m < 12 else (last_december if (last_december and y == years - 1) else december))
            rows.append((f"{2020 + y}-{m:02d}", v))
    return rows


def _judge(rows):
    months = [int(p[:4]) * 12 + int(p[5:]) for p, _ in rows]
    values = [v for _, v in rows]
    moms = [None] + [(values[i] - values[i - 1]) / abs(values[i - 1]) * 100 for i in range(1, len(values))]
    return dict(zip([p for p, _ in rows], judge_anomalies(months, moms, 2.0)))


def test_first_december_spike_is_flagged_month_on_month():
    first = _judge(_series(1))["2020-12"]
    assert first[0] is True and first[1] == "month_on_month"


def test_a_spike_every_december_stops_being_an_anomaly():
    j = _judge(_series(4))
    assert j["2023-12"][1] == "seasonal" and j["2023-12"][0] is False
    assert j["2022-12"][0] is False


def test_an_unusual_december_still_flags_against_its_own_season():
    j = _judge(_series(4, last_december=2.2))
    assert j["2023-12"] == (True, "seasonal", j["2023-12"][2]) and j["2023-12"][2] > 2


def test_too_little_history_is_not_judged():
    j = _judge(_series(1)[:4])
    assert all(v == (False, None, None) for v in j.values())


def _csv(rows, budget=None):
    lines = ["period,metric,value,budget"]
    for p, v in rows:
        lines.append(f"{p},Revenue,{v},{'' if budget is None else budget(p, v)}")
    return "\n".join(lines) + "\n"


def test_facts_expose_basis_z_and_threshold(client):
    _import(client, _csv(_series(3)))
    dec = client.get("/facts?period=2022-12").json()[0]
    assert dec["anomaly"]["basis"] == "seasonal" and dec["anomaly"]["threshold"] == 2.0
    assert isinstance(dec["anomaly"]["z"], float)
    assert client.get("/facts?period=2020-02").json()[0]["anomaly"] is None
    assert client.get("/facts?period=2022-Q4&granularity=quarter").json()[0]["anomaly"] is None


def test_reason_threshold_is_per_dataset(client):
    # 15% over plan in March 2020, otherwise on plan.
    ds = _import(client, _csv(_series(1)[:6], budget=lambda p, v: v / 1.15 if p == "2020-03" else v),
                 {**LONG, "budget_col": "budget"})["dataset_id"]
    mar = client.get("/facts?period=2020-03").json()[0]
    assert mar["needs_reason_flag"] is True
    assert next(d for d in client.get("/datasets").json()["datasets"] if d["id"] == ds)["reason_threshold_pct"] == 10.0
    assert next(m for m in client.get("/close/months").json() if m["period"] == "2020-03")["needs_reason"] == 1
    r = client.patch(f"/datasets/{ds}", json={"reason_threshold_pct": 25})
    assert r.status_code == 200 and r.json()["reason_threshold_pct"] == 25.0
    assert client.get("/facts?period=2020-03").json()[0]["needs_reason_flag"] is False
    assert next(m for m in client.get("/close/months").json() if m["period"] == "2020-03")["needs_reason"] == 0


@pytest.mark.parametrize("bad", [0, 101, "10", True, None])
def test_reason_threshold_rejects_invalid_values(client, bad):
    ds = _import(client, _csv(_series(1)[:3]))["dataset_id"]
    assert client.patch(f"/datasets/{ds}", json={"reason_threshold_pct": bad}).status_code == 422


def test_old_rule_flags_are_recomputed_once(client):
    from app.compute.kpis import refresh_stale_anomalies
    from app.db import get_connection

    _import(client, _csv(_series(3)))
    conn = get_connection()
    try:
        # Simulate facts written by the old rule: flagged, with no basis.
        conn.execute("UPDATE computed_facts SET is_anomaly = 1, anomaly_basis = NULL, anomaly_z = NULL")
        conn.commit()
        assert refresh_stale_anomalies(conn) == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM computed_facts WHERE is_anomaly = 1 AND anomaly_basis IS NULL"
                            ).fetchone()["n"] == 0
        assert refresh_stale_anomalies(conn) == 0
    finally:
        conn.close()
