"""Close locks protect stored figures and commentary; corrections remain traceable."""

from test_datasets import client  # noqa: F401
from test_import_honesty import LONG, _import, _upload

from app.db import get_connection
from app.scheduling import record_digest_run


def _replace(api, dataset, text):
    upload = _upload(api, text)
    response = api.post(f"/ingest/{upload}/mapping", params={"target_dataset_id": dataset}, json=LONG)
    assert response.status_code == 200, response.text
    assert response.json()["dataset_id"] == dataset
    return response.json()


def _changes(api, period="2025-Q1", granularity="quarter"):
    response = api.get("/close/changes", params={"period": period, "granularity": granularity})
    assert response.status_code == 200, response.text
    return response.json()


def _share(dataset, period="2025-01"):
    conn = get_connection()
    try:
        record_digest_run(conn, dataset, period, 5, [], 0, "manual")
    finally:
        conn.close()


def _values():
    conn = get_connection()
    try:
        return {(r["name"], r["period"]): r["value"] for r in conn.execute(
            "SELECT m.name, mv.period, mv.value FROM metric_values mv JOIN metrics m ON m.id=mv.metric_id"
        ).fetchall()}
    finally:
        conn.close()


def test_first_import_corrections_removals_and_identical_reimport(client):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n2025-01,Cost,40\n")["dataset_id"]
    assert _changes(client) == {"changes": [], "total": 0}
    text = "period,metric,value\n2025-01,Revenue,110\n"
    _replace(client, ds, text)
    changes = _changes(client)
    assert changes["total"] == 2
    assert {(r["metric"], r["old_value"], r["new_value"], r["field"], r["source"]) for r in changes["changes"]} == {
        ("Revenue", 100, 110, "value", "import"), ("Cost", 40, None, "value", "import")}
    _replace(client, ds, text)
    assert _changes(client)["total"] == 2


def test_budget_join_logged_and_close_month_filter(client):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n2025-02,Revenue,100\n"
                 "2025-03,Revenue,100\n2025-04,Revenue,100\n")["dataset_id"]
    upload = _upload(client, "period,metric,value\n2025-01,Revenue,90\n2025-02,Revenue,80\n"
                     "2025-03,Revenue,70\n2025-04,Revenue,60\n")
    response = client.post(f"/ingest/{upload}/join-budget", json={"target_dataset_id": ds, "mapping": LONG})
    assert response.status_code == 200, response.text
    result = _changes(client)
    assert result["total"] == 3
    assert {r["period"] for r in result["changes"]} == {"2025-01", "2025-02", "2025-03"}
    assert all(r["field"] == "budget" and r["source"] == "budget_join" and r["old_value"] is None
               for r in result["changes"])
    assert _changes(client, "2025-04", "month")["total"] == 1
    assert _changes(client, "2025", "year")["total"] == 4


def test_lock_requires_share_skips_values_reports_and_unlocks(client, monkeypatch):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n2025-02,Revenue,200\n")["dataset_id"]
    body = {"period": "2025-01", "granularity": "month"}
    response = client.post("/close/lock", json=body)
    assert response.status_code == 409
    assert response.json()["detail"] == "Send the digest before locking this close."
    _share(ds)
    assert client.post("/close/lock", json=body).status_code == 200
    assert client.post("/close/lock", json=body).status_code == 200
    status = client.get("/close/status?period=2025-01").json()
    assert status["locked"] and status["locked_at"]
    assert next(r for r in client.get("/close/months").json() if r["period"] == "2025-01")["locked"]
    result = _replace(client, ds, "period,metric,value\n2025-01,Revenue,150\n2025-02,Revenue,250\n")
    assert result["import_report"]["locked_skipped"] == [{"period": "2025-01", "rows": 1}]
    assert _values() == {("Revenue", "2025-01"): 100, ("Revenue", "2025-02"): 250}
    assert [(r["period"], r["old_value"], r["new_value"]) for r in _changes(client)["changes"]] == [
        ("2025-02", 200, 250)]
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id=?", (ds,)).fetchone()["id"]
        rid = conn.execute("INSERT INTO generated_reports (metric_id, period, confidence, faithfulness) "
                           "VALUES (?, '2025-01', 'High', 'passed')", (mid,)).lastrowid
        conn.commit()
    finally:
        conn.close()
    request = {"metric": "Revenue", "period": "2025-01", "use_retrieval": False}
    for force in (False, True):
        response = client.post("/generate-insight", json={**request, "force": force})
        assert response.status_code == 409, response.text
    assert client.post(f"/reports/{rid}/review", json={"status": "approved"}).status_code == 409
    assert client.post("/feedback", json={"report_id": rid, "action": "edited", "edited_text": "Changed"}).status_code == 409
    assert client.post(f"/reports/{rid}/assign", json={}).status_code == 409
    assert client.post("/close/unlock", json=body).status_code == 200
    assert not client.get("/close/status?period=2025-01").json()["locked"]
    assert client.post(f"/reports/{rid}/review", json={"status": "approved"}).status_code == 200
    assert client.post("/feedback", json={"report_id": rid, "action": "edited", "edited_text": "Changed"}).status_code == 201
    # Stop at the generation boundary: no LLM calls, while proving the lock guard allows it.
    from fastapi import HTTPException

    from app.routers import generate
    def offline():
        raise HTTPException(status_code=418, detail="offline generation boundary")
    monkeypatch.setattr(generate, "get_llm_client", offline)
    response = client.post("/generate-insight", json={**request, "force": True})
    assert response.status_code == 418, response.text


def test_changes_since_shared_uses_timestamp_and_close_months(client):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n2025-02,Revenue,200\n")["dataset_id"]
    _replace(client, ds, "period,metric,value\n2025-01,Revenue,110\n2025-02,Revenue,210\n")
    assert client.get("/close/status?period=2025-01").json()["changes_since_shared"] == 0
    _share(ds)
    _replace(client, ds, "period,metric,value\n2025-01,Revenue,120\n2025-02,Revenue,220\n")
    assert client.get("/close/status?period=2025-01").json()["changes_since_shared"] == 1



def test_locked_budget_join_receipt_and_unchanged_repeat(client):  # noqa: F811
    ds = _import(client, "period,metric,value,budget\n2025-01,Revenue,100,90\n2025-02,Revenue,200,180\n",
                 {**LONG, "budget_col": "budget"})["dataset_id"]
    _share(ds)
    assert client.post("/close/lock", json={"period": "2025-01"}).status_code == 200
    upload = _upload(client, "period,metric,value\n2025-01,Revenue,95\n2025-02,Revenue,190\n")
    body = {"target_dataset_id": ds, "mapping": LONG}
    for _ in range(2):
        response = client.post(f"/ingest/{upload}/join-budget", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["locked_skipped"] == [{"period": "2025-01", "rows": 1}]
        assert response.json()["import_report"]["locked_skipped"] == [{"period": "2025-01", "rows": 1}]
    rows = _changes(client)["changes"]
    assert [(r["field"], r["old_value"], r["new_value"], r["source"]) for r in rows] == [
        ("budget", 180, 190, "budget_join")]
    receipt = client.get(f"/datasets/{ds}/receipts").json()[0]
    assert receipt["report"]["locked_skipped"] == [{"period": "2025-01", "rows": 1}]
    assert receipt["kpi_totals"][0]["budget_total"] == 190


def test_connector_and_custom_kpi_skip_locks_and_log_sources(client):  # noqa: F811
    from app import connectors
    from app.compute import derived

    conn = get_connection()
    try:
        cid = connectors.create_connector(conn, "csv_url", "test", {"url": "https://example.com/data.csv"}, "synced", None)
        def first(_url):
            return b"period,metric,value,budget\n2025-01,Revenue,100,90\n2025-02,Revenue,200,180\n"
        initial = connectors.sync_connector(conn, cid, None, fetch=first)
        assert initial["status"] == "ok"
        ds = initial["dataset_id"]
        assert _changes(client)["total"] == 0
        derived.create_derived_metric(conn, ds, "Double", "[Revenue]*2")
        derived.refresh(conn, ds)
    finally:
        conn.close()
    baseline = _changes(client)["total"]
    _share(ds)
    assert client.post("/close/lock", json={"period": "2025-01"}).status_code == 200
    conn = get_connection()
    try:
        def second(_url):
            return b"period,metric,value,budget\n2025-01,Revenue,150,95\n2025-02,Revenue,250,190\n"
        result = connectors.sync_connector(conn, cid, None, fetch=second)
        assert result["status"] == "ok", result
        assert result["import_report"]["locked_skipped"] == [{"period": "2025-01", "rows": 2}]
    finally:
        conn.close()
    values = _values()
    assert values == {("Revenue", "2025-01"): 100, ("Revenue", "2025-02"): 250,
                      ("Double", "2025-01"): 200, ("Double", "2025-02"): 500}
    changes = _changes(client)
    assert changes["total"] == baseline + 3
    latest = changes["changes"][:3]
    assert {(r["metric"], r["field"], r["source"]) for r in latest} == {
        ("Revenue", "value", "sync"), ("Revenue", "budget", "sync"), ("Double", "value", "custom_kpi")}
    assert all(r["period"] == "2025-02" for r in latest)
    receipt = client.get(f"/datasets/{ds}/receipts").json()[0]
    assert receipt["report"]["locked_skipped"] == [{"period": "2025-01", "rows": 2}]
    assert all(r["periods"] == 1 for r in receipt["kpi_totals"])


def test_remap_same_upload_tracks_changes_and_locked_deletions_are_skipped(client):  # noqa: F811
    upload = _upload(client, "period,metric,value,corrected\n2025-01,Revenue,100,150\n2025-02,Revenue,200,250\n")
    first = client.post(f"/ingest/{upload}/mapping", json=LONG).json()
    ds = first["dataset_id"]
    response = client.post(f"/ingest/{upload}/mapping", json={**LONG, "value_col": "corrected"})
    assert response.status_code == 200, response.text
    assert response.json()["dataset_id"] == ds
    assert _changes(client)["total"] == 2
    _share(ds)
    client.post("/close/lock", json={"period": "2025-01"})
    result = _replace(client, ds, "period,metric,value\n2025-02,Revenue,250\n")
    assert result["import_report"]["locked_skipped"] == [{"period": "2025-01", "rows": 1}]
    assert _values()[("Revenue", "2025-01")] == 150
    assert _changes(client)["total"] == 2
    receipt = client.get(f"/datasets/{ds}/receipts").json()[0]
    assert receipt["report"]["locked_skipped"] == [{"period": "2025-01", "rows": 1}]


def test_change_log_failure_rolls_back_values_and_partial_log(client, monkeypatch):  # noqa: F811
    from app import close_control

    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n")["dataset_id"]
    original = close_control.record_changes
    def fail_after_logging(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("failed change log transaction")
    monkeypatch.setattr(close_control, "record_changes", fail_after_logging)
    import pytest
    with pytest.raises(RuntimeError, match="failed change log transaction"):
        _replace(client, ds, "period,metric,value\n2025-01,Revenue,120\n")
    assert _values() == {("Revenue", "2025-01"): 100}
    assert _changes(client)["total"] == 0


def test_numeric_tolerance(client):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-01,Revenue,1\n")["dataset_id"]
    _replace(client, ds, "period,metric,value\n2025-01,Revenue,1.0000000001\n")
    assert _changes(client)["total"] == 0
    _replace(client, ds, "period,metric,value\n2025-01,Revenue,1.000000002\n")
    assert _changes(client)["total"] == 1


def test_quarter_lock_covers_empty_months_and_fiscal_changes_filter(client):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-04,Revenue,100\n2025-07,Revenue,200\n")["dataset_id"]
    conn = get_connection()
    try:
        conn.execute("UPDATE datasets SET fiscal_year_start=4 WHERE id=?", (ds,))
        conn.commit()
    finally:
        conn.close()
    _share(ds, "2025-04")
    body = {"period": "2026-Q1", "granularity": "quarter"}
    response = client.post("/close/lock", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["locked"]
    result = _replace(client, ds, "period,metric,value\n2025-04,Revenue,110\n2025-05,Revenue,120\n2025-07,Revenue,210\n")
    assert result["import_report"]["locked_skipped"] == [
        {"period": "2025-04", "rows": 1}, {"period": "2025-05", "rows": 1}]
    assert _changes(client, "2026-Q1", "quarter")["total"] == 0
    assert _changes(client, "2026-Q2", "quarter")["total"] == 1
    assert client.post("/close/unlock", json=body).status_code == 200
    assert not client.get("/close/status?period=2026-Q1").json()["locked"]



def test_prior_month_update_does_not_reopen_locked_commentary(client):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n2025-02,Revenue,200\n")["dataset_id"]
    _share(ds, "2025-02")
    assert client.post("/close/lock", json={"period": "2025-02"}).status_code == 200
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id=?", (ds,)).fetchone()["id"]
        rid = conn.execute("""INSERT INTO generated_reports
            (metric_id, period, confidence, faithfulness, review_status)
            VALUES (?, '2025-02', 'High', 'passed', 'approved')""", (mid,)).lastrowid
        conn.commit()
    finally:
        conn.close()
    _replace(client, ds, "period,metric,value\n2025-01,Revenue,110\n2025-02,Revenue,250\n")
    conn = get_connection()
    try:
        row = conn.execute("SELECT review_status, faithfulness FROM generated_reports WHERE id=?", (rid,)).fetchone()
        assert (row["review_status"], row["faithfulness"]) == ("approved", "passed")
    finally:
        conn.close()


def test_lock_request_rejects_non_string_period(client):  # noqa: F811
    _import(client, "period,metric,value\n2025-01,Revenue,100\n")
    for route in ("/close/lock", "/close/unlock"):
        for body in ({"period": 2025}, {"period": ["2025-01"]}, {"period": "2025-Q1", "granularity": "month"}):
            response = client.post(route, json=body)
            assert response.status_code == 422, response.text



def test_feedback_and_versions_share_the_lock_transaction(client, monkeypatch):  # noqa: F811
    import pytest

    from app import review

    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n")["dataset_id"]
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id=?", (ds,)).fetchone()["id"]
        rid = conn.execute("""INSERT INTO generated_reports (metric_id, period, narrative, confidence, faithfulness)
                              VALUES (?, '2025-01', 'Original', 'High', 'passed')""", (mid,)).lastrowid
        conn.commit()
    finally:
        conn.close()
    def broken_version(*args, **kwargs):
        raise RuntimeError("version failure")
    monkeypatch.setattr(review, "add_version", broken_version)
    with pytest.raises(RuntimeError, match="version failure"):
        client.post("/feedback", json={"report_id": rid, "action": "edited", "edited_text": "Changed"})
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM feedback WHERE report_id=?", (rid,)).fetchone()["n"] == 0
    finally:
        conn.close()


def test_deletion_with_only_locked_incoming_rows_updates_freshness(client):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n2025-02,Revenue,200\n")["dataset_id"]
    before = client.get("/datasets").json()["datasets"][0]["data_changed_at"]
    _share(ds)
    client.post("/close/lock", json={"period": "2025-01"})
    _replace(client, ds, "period,metric,value\n2025-01,Revenue,150\n")
    after = client.get("/datasets").json()["datasets"][0]["data_changed_at"]
    assert after != before
    assert _values() == {("Revenue", "2025-01"): 100}
    assert [(r["period"], r["new_value"]) for r in _changes(client)["changes"]] == [("2025-02", None)]



def test_budget_cleared_by_reimport_records_null_transition(client):  # noqa: F811
    ds = _import(client, "period,metric,value,budget\n2025-01,Revenue,100,90\n",
                 {**LONG, "budget_col": "budget"})["dataset_id"]
    _replace(client, ds, "period,metric,value\n2025-01,Revenue,100\n")
    assert [(r["field"], r["old_value"], r["new_value"], r["source"]) for r in _changes(client)["changes"]] == [
        ("budget", 90, None, "import")]


def test_changes_newest_first_capped_but_total_includes_all_and_scoped(client):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n")["dataset_id"]
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id=?", (ds,)).fetchone()["id"]
        for i in range(505):
            conn.execute("""INSERT INTO value_changes
                (dataset_id, metric_id, period, field, old_value, new_value, source, actor_email, created_at)
                VALUES (?, ?, '2025-01', 'value', ?, ?, 'import', 'controller@example.test', '2025-01-31T12:00:00+00:00')""",
                         (ds, mid, i, i + 1))
        conn.commit()
    finally:
        conn.close()
    result = _changes(client)
    assert result["total"] == 505 and len(result["changes"]) == 500
    assert [r["new_value"] for r in result["changes"]] == list(range(505, 5, -1))
    assert result["changes"][0]["actor"] == "controller@example.test"
    _import(client, "period,metric,value\n2025-01,Other,10\n")
    assert _changes(client) == {"changes": [], "total": 0}



def test_locked_generation_returns_lock_conflict_before_ai_budget_check(client, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from app.routers import generate

    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n")["dataset_id"]
    _share(ds)
    client.post("/close/lock", json={"period": "2025-01"})
    def exhausted(_user):
        raise HTTPException(status_code=429, detail="AI allowance exhausted")
    monkeypatch.setattr(generate, "_enforce_budget", exhausted)
    response = client.post("/generate-insight", json={"metric": "Revenue", "period": "2025-01", "force": True})
    assert response.status_code == 409, response.text
