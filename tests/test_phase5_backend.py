"""Backend close exports match dashboard figures and never disclose webhook secrets."""

import csv
import io
import json
from datetime import datetime
from urllib.parse import unquote

import pytest
from openpyxl import load_workbook
from test_datasets import client  # noqa: F401
from test_import_honesty import LONG, _import

from app.close_export import _timestamp
from app.db import get_connection

INJECTION = '=HYPERLINK("http://x")'
PARAMS = {"period": "2025-12", "granularity": "month"}


def _book(api, **params):
    response = api.get("/close/export.xlsx", params=params or PARAMS)
    assert response.status_code == 200, response.text
    return load_workbook(io.BytesIO(response.content))


def _table(sheet):
    headers = [c.value for c in sheet[1]]
    return [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2)]


@pytest.fixture
def seeded(client):  # noqa: F811
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(["period", "metric", "value", "budget", "forecast", "Region"])
    for period in ("2024-12", "2025-11", "2025-12"):
        for name, value, budget, forecast in [
            ("Revenue", 1000, 900, 1100), ("Margin", 38, 40, None),
            ("Orders", 12, 10, None), ("Days", 8.5, 7, None), (INJECTION, 10, 9, None),
        ]:
            if name == "Revenue":
                writer.writerow([period, name, value / 2, budget / 2, forecast / 2, "=East"])
                writer.writerow([period, name, value / 2, budget / 2, forecast / 2, "West"])
            else:
                writer.writerow([period, name, value, budget, forecast, "East"])
    ds = _import(client, text.getvalue(), {**LONG, "budget_col": "budget", "forecast_col": "forecast",
                                         "dimension_cols": ["Region"]})["dataset_id"]
    response = client.post("/kpis", json={"kpis": [
        {"source_metric": name, "display_name": name, "category": "@Finance", "unit": unit,
         "direction_good": "up", "aggregation_type": "ratio" if unit == "%" else "flow"}
        for name, unit in [("Revenue", "USD"), ("Margin", "%"), ("Orders", "count"),
                           ("Days", "days"), (INJECTION, "number"), ("Missing", "USD")]
    ]})
    assert response.status_code == 200, response.text
    conn = get_connection()
    try:
        conn.execute("UPDATE datasets SET name='Café:/Close?', currency='EUR', data_changed_at=? WHERE id=?",
                     ("2025-12-31T12:00:00+00:00", ds))
        conn.execute("UPDATE metrics SET name=? WHERE dataset_id=? AND name=?", (INJECTION, ds, INJECTION[1:]))
        conn.execute("UPDATE kpi_configs SET source_metric=?, display_name=? WHERE dataset_id=? AND source_metric=?",
                     (INJECTION, INJECTION, ds, INJECTION[1:]))
        ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM metrics WHERE dataset_id=?", (ds,)).fetchall()}
        for name, status, check, narrative in [
            ("Revenue", "approved", "passed", "=Approved revenue"),
            ("Margin", "pending", "failed", "Unapproved margin"),
            ("Orders", None, "unchecked", "Unapproved orders"),
        ]:
            conn.execute("""INSERT INTO generated_reports
                (metric_id, period, narrative, confidence, faithfulness, review_status)
                VALUES (?, '2025-12', ?, 'High', ?, ?)""", (ids[name], narrative, check, status))
        conn.commit()
    finally:
        conn.close()
    return client, ds, ids


def test_export_contract_figures_formats_and_audit(seeded):
    api, ds, _ = seeded
    response = api.get("/close/export.xlsx", params=PARAMS)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="CloseBrief - ')
    assert "filename*=UTF-8''" in disposition
    assert unquote(disposition.split("filename*=UTF-8''")[1]) == "CloseBrief - CaféClose - December 2025 close.xlsx"
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["Summary", "KPIs", "Contributors", "Receipt"]
    facts = api.get("/facts", params=PARAMS).json()
    rows = _table(book["KPIs"])
    assert [r["KPI"].value for r in rows] == [f["metric"] for f in facts]
    for row, fact in zip(rows, facts):
        divisor = 100 if fact["unit"] == "%" else 1
        assert row["Actual"].value == (fact["value"] / divisor if fact["value"] is not None else None)
        plan = (fact.get("chart_data") or {}).get("budget_vs_actual") or {}
        assert row["Plan"].value == (plan["budget"] / divisor if plan.get("budget") is not None else None)
        assert row["Variance"].value == fact["deltas"]["budget_var_abs"]
        if row["Actual"].value is not None:
            assert row["Actual"].data_type == "n"
    by_name = {r["KPI"].value: r for r in rows}
    assert by_name["Revenue"]["Actual"].value == 1000
    assert by_name["Revenue"]["Actual"].number_format == "#,##0.00"
    assert by_name["Revenue"]["Variance %"].value == pytest.approx(round(100 / 900, 4))
    assert by_name["Margin"]["Actual"].number_format == "0.0%"
    assert by_name["Margin"]["Variance"].number_format == '0.0" pts"'
    assert by_name["Margin"]["Variance"].value == -2
    for column in ("Variance %", "Vs forecast %", "Vs prior %", "Vs last year %"):
        assert by_name["Margin"][column].value is None
    assert by_name["Orders"]["Actual"].number_format == "#,##0"
    assert by_name["Days"]["Actual"].number_format == "0.0"
    assert by_name["Days"]["Variance"].number_format == '0.0" days"'
    assert by_name["Revenue"]["Commentary"].value == "=Approved revenue"
    assert by_name["Revenue"]["Status"].value == "Approved"
    assert by_name["Margin"]["Status"].value == "Draft"
    assert by_name["Days"]["Status"].value == "Not drafted"
    assert by_name["Missing"]["Status"].value == "No data"
    assert by_name["Revenue"]["Figures check"].value == "Checked"
    assert by_name["Margin"]["Figures check"].value == "Needs review"
    assert by_name["Orders"]["Figures check"].value == "Changed since written"
    assert by_name["Days"]["Figures check"].value == "—"
    assert all(r["Commentary"].value is None for r in rows if r["KPI"].value != "Revenue")
    assert by_name[INJECTION]["KPI"].value == INJECTION
    assert by_name[INJECTION]["KPI"].data_type == "s"
    assert all(c.data_type != "f" for sheet in book for row in sheet for c in row)
    sheet = book["KPIs"]
    assert sheet.freeze_panes == "C2"
    assert all(c.font.bold for c in sheet[1])
    assert sheet.auto_filter.ref == sheet.dimensions
    assert by_name["Revenue"]["Commentary"].alignment.wrap_text
    conn = get_connection()
    try:
        entry = conn.execute("SELECT * FROM audit_log WHERE action='export'").fetchone()
        assert entry["entity_type"] == "close" and entry["entity_id"] == str(ds)
        assert json.loads(entry["summary"]) == {"period": "2025-12"}
    finally:
        conn.close()
    assert any(a["text"] == "Exported the December 2025 close"
               for a in api.get("/close/status", params=PARAMS).json()["activity"])


def test_summary_receipt_and_contributors(seeded):
    api, _, _ = seeded
    book = _book(api)
    summary = {r[0].value: r[1].value for r in book["Summary"] if len(r) >= 2}
    assert summary["Close"] == "December 2025 close"
    assert summary["Dataset"] == "Café:/Close?"
    assert summary["Currency"] == "EUR"
    assert summary["Fiscal year start month"] == 1
    assert summary["Data as of"] == "31 Dec 2025 12:00 UTC"
    assert summary["Close status"] == "In progress"
    assert summary["KPIs"] == 5
    assert (summary["Drafted"], summary["Approved"], summary["Figures checked"]) == (3, 1, 1)
    assert summary["Needs a reason"] == 4
    assert datetime.strptime(summary["Generated at (UTC)"], "%d %b %Y %H:%M UTC")
    assert any(c.value == "Figures are computed in code. Commentary is included only once approved."
               for row in book["Summary"] for c in row)
    rows = _table(book["Contributors"])
    revenue = [r for r in rows if r["KPI"].value == "Revenue"]
    assert len(revenue) == 2
    assert {r["Part"].value for r in revenue} == {"=East", "West"}
    assert all(r["Breakdown"].value == "Region" for r in revenue)
    assert sum(r["Actual"].value for r in revenue) == 1000
    assert sum(r["Plan"].value for r in revenue) == 900
    receipt = list(book["Receipt"].values)
    assert receipt[0] == ("When", "Kind", "File", "Rows read", "Rows kept")
    assert receipt[1][2:] == ("honesty.csv", 18, 18)
    totals_header = receipt.index(("KPI", "Months", "Actual total", "Budget total", "Rows"))
    totals = {r[0]: r for r in receipt[totals_header + 1:]}
    assert totals["Revenue"] == ("Revenue", 3, 3000, 2700, 6)


@pytest.mark.parametrize("granularity,period", [("quarter", "2025-Q4"), ("year", "2025")])
def test_rollup_export_uses_facts_and_omits_ytd(seeded, granularity, period):
    api, _, _ = seeded
    book = _book(api, period=period, granularity=granularity)
    headers = [c.value for c in book["KPIs"][1]]
    assert not any(h.startswith("YTD") for h in headers)
    facts = api.get("/facts", params={"period": period, "granularity": granularity}).json()
    for row, fact in zip(_table(book["KPIs"]), facts):
        assert row["KPI"].value == fact["metric"]
        # Excel stores percentage-formatted cells as fractions; ratio rollups
        # now have values, so apply the same contract as the monthly export.
        expected = fact["value"] / 100 if fact["unit"] == "%" and fact["value"] is not None else fact["value"]
        assert row["Actual"].value == expected
        assert row["Variance"].value == fact["deltas"]["budget_var_abs"]
    assert "Forecast" in headers


def test_optional_sheets_and_columns(client):  # noqa: F811
    _import(client, "period,metric,value\n2025-12,Revenue,100\n")
    book = _book(client)
    assert book.sheetnames == ["Summary", "KPIs", "Receipt"]
    headers = [c.value for c in book["KPIs"][1]]
    assert "Forecast" not in headers and "Vs forecast %" not in headers
    assert "YTD actual" in headers
    response = client.post("/kpis", json={"kpis": [{"source_metric": "Revenue", "display_name": "Revenue",
        "category": "Test", "unit": "%", "direction_good": "up", "aggregation_type": "ratio"}]})
    assert response.status_code == 200
    assert not any(c.value.startswith("YTD") for c in _book(client)["KPIs"][1])


def test_changes_match_close_changes(seeded):
    api, ds, ids = seeded
    conn = get_connection()
    try:
        conn.execute("""INSERT INTO value_changes
            (dataset_id, metric_id, period, field, old_value, new_value, source, actor_email)
            VALUES (?, ?, '2025-12', 'budget', 800, 900, 'import', '+owner@example.com')""", (ds, ids["Revenue"]))
        conn.commit()
    finally:
        conn.close()
    book = _book(api)
    assert book.sheetnames == ["Summary", "KPIs", "Contributors", "Changes", "Receipt"]
    change = api.get("/close/changes", params=PARAMS).json()["changes"][0]
    assert list(book["Changes"].values)[1] == tuple((_timestamp(change[k]) if k == "at" else change[k]) for k in
        ("at", "metric", "period", "field", "old_value", "new_value", "source", "actor"))
    assert all(c.data_type != "f" for row in book["Changes"] for c in row)


def test_export_without_dataset(client):  # noqa: F811
    response = client.get("/close/export.xlsx", params=PARAMS)
    assert response.status_code == 404
    assert response.json()["detail"] == "No active dataset"


@pytest.mark.parametrize("period,granularity", [("bad", "month"), ("2025-13", "month"),
    ("0000-12", "month"), ("2025-Q4", "month"), ("2025-12", "quarter"), ("2025", "invalid")])
def test_export_rejects_invalid_selection(seeded, period, granularity):
    assert seeded[0].get("/close/export.xlsx", params={"period": period, "granularity": granularity}).status_code == 422


def test_recipients_filter_and_redaction(client):  # noqa: F811
    assert client.get("/digest/recipients").json() == []
    configs = [
        ("email", {"recipients": ["a@example.com", "b@example.com"], "secret": "NEVER"}, True),
        ("slack", {"webhook_url": "https://hooks.slack.example/secretABCD", "events": ["digest_generated"]}, True),
        ("webhook", {"url": "https://hooks.example/secretEFGH", "events": []}, True),
        ("teams", {"webhook_url": "https://teams.example/secretIJKL"}, True),
        ("email", {"recipients": ["disabled@example.com"]}, False),
        ("email", {"recipients": ["alerts@example.com"], "events": ["anomaly_detected"]}, True),
        ("other", {"secret": "NEVER", "url": "https://other.example/token"}, True),
    ]
    conn = get_connection()
    try:
        ids = []
        for channel, config, enabled in configs:
            ids.append(conn.execute("INSERT INTO notification_configs (channel, config, enabled) VALUES (?, ?, ?) RETURNING id",
                                    (channel, json.dumps(config), enabled)).fetchone()["id"])
        conn.commit()
    finally:
        conn.close()
    response = client.get("/digest/recipients")
    assert response.status_code == 200
    assert response.json() == [
        {"id": ids[0], "channel": "email", "label": "a@example.com, b@example.com"},
        {"id": ids[1], "channel": "slack", "label": "Slack webhook (…ABCD)"},
        {"id": ids[2], "channel": "webhook", "label": "Webhook webhook (…EFGH)"},
        {"id": ids[3], "channel": "teams", "label": "Teams webhook (…IJKL)"},
        {"id": ids[6], "channel": "other", "label": "other"},
    ]
    for _, config, _ in configs:
        url = config.get("url") or config.get("webhook_url")
        if url:
            assert url not in response.text and url.split("/")[2] not in response.text
    assert "NEVER" not in response.text


def test_viewer_cannot_preview_recipients_but_can_export(seeded, monkeypatch):
    from app import auth
    from app.auth import CurrentUser

    monkeypatch.setattr(auth, "get_current_user", lambda request: CurrentUser(
        id="00000000-0000-0000-0000-000000000001", email="viewer@example.com", role="viewer"))
    api = seeded[0]
    assert api.get("/digest/recipients").status_code == 403
    assert api.get("/close/export.xlsx", params=PARAMS).status_code == 200


def test_summary_statuses_and_locked_attribution(client):  # noqa: F811
    from app.scheduling import record_digest_run

    ds = _import(client, "period,metric,value,budget\n2025-12,Revenue,100,100\n",
                 {**LONG, "budget_col": "budget"})["dataset_id"]

    def summary():
        return {r[0]: r[1] for r in _book(client)["Summary"].values}

    assert summary()["Close status"] == "Not started"
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id=?", (ds,)).fetchone()["id"]
        conn.execute("""INSERT INTO generated_reports
            (metric_id, period, narrative, confidence, faithfulness, review_status)
            VALUES (?, '2025-12', 'Approved', 'High', 'passed', 'approved')""", (mid,))
        conn.commit()
        assert summary()["Close status"] == "Ready"
        record_digest_run(conn, ds, "2025-12", 5, [], None, "sent")
    finally:
        conn.close()
    assert summary()["Close status"] == "Shared"
    assert summary()["Digest sent date"]
    response = client.post("/close/lock", json=PARAMS)
    assert response.status_code == 200, response.text
    lock = response.json()
    assert summary()["Lock"] == f"Locked on {_timestamp(lock['locked_at'])} by {lock['locked_by']}"


def test_text_prefixes_across_all_sheets_stay_strings(seeded):
    api, ds, ids = seeded
    conn = get_connection()
    try:
        conn.execute("UPDATE datasets SET name='=Dataset' WHERE id=?", (ds,))
        conn.execute("UPDATE kpi_configs SET category='+Finance' WHERE dataset_id=?", (ds,))
        conn.execute("UPDATE generated_reports SET narrative='-approved' WHERE metric_id=?", (ids["Revenue"],))
        conn.execute("UPDATE metric_breakdowns SET member='@West' WHERE metric_id=? AND member='West'", (ids["Revenue"],))
        conn.execute("UPDATE import_receipts SET filename=? WHERE dataset_id=?", ("\tfile.csv", ds))
        conn.execute("""INSERT INTO value_changes
            (dataset_id, metric_id, period, field, old_value, new_value, source, actor_email)
            VALUES (?, ?, '2025-12', 'value', 900, 1000, 'import', ?)""", (ds, ids["Revenue"], "\rperson@example.com"))
        conn.commit()
    finally:
        conn.close()
    book = _book(api)
    strings = {c.value: c for sheet in book for row in sheet for c in row if isinstance(c.value, str)}
    for value in ("=Dataset", "+Finance", "-approved", "@West", "\tfile.csv", INJECTION):
        assert strings[value].data_type == "s"
    # XML normalizes a literal carriage return to a newline on round-trip.
    actor = book["Changes"]["H2"]
    assert actor.data_type == "s" and actor.value.lstrip("\r\n") == "person@example.com"
    assert not any(c.data_type == "f" for sheet in book for row in sheet for c in row)


def test_recipients_are_workspace_scoped(client):  # noqa: F811
    from app import datasets, workspaces
    from app.auth import ANONYMOUS_ADMIN
    from app.routers.notifications import digest_recipients

    conn = get_connection()
    try:
        ws = workspaces.create_workspace(conn, "Workspace A", ANONYMOUS_ADMIN.id)
        other = workspaces.create_workspace(conn, "Workspace B", ANONYMOUS_ADMIN.id)
        for scope, address in [(None, "local@example.com"), (ws, "a@example.com"), (other, "b@example.com")]:
            conn.execute("INSERT INTO notification_configs (channel, config, workspace_id) VALUES ('email', ?, ?)",
                         (json.dumps({"recipients": [address]}), scope))
        conn.commit()
    finally:
        conn.close()
    token = datasets._workspace_scope.set(ws)
    try:
        assert [r["label"] for r in digest_recipients(ANONYMOUS_ADMIN)] == ["a@example.com"]
    finally:
        datasets._workspace_scope.reset(token)


def test_export_uses_selected_active_dataset(seeded):
    api, ds, _ = seeded
    _import(api, "period,metric,value\n2025-12,Other,42\n")
    assert [r["KPI"].value for r in _table(_book(api)["KPIs"])] == ["Other"]
    response = api.get("/close/export.xlsx", params=PARAMS, headers={"X-Dataset-Id": str(ds)})
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content))
    names = [r["KPI"].value for r in _table(book["KPIs"])]
    assert "Revenue" in names and "Other" not in names


def test_duplicate_display_names_keep_distinct_rollup_contributors(client):  # noqa: F811
    _import(client, "period,metric,value,budget,Region\n2025-12,A,2,1,East\n2025-12,A,4,1,West\n2025-12,B,10,20,North\n2025-12,B,20,40,South\n",
            {**LONG, "budget_col": "budget", "dimension_cols": ["Region"]})
    response = client.post("/kpis", json={"kpis": [
        {"source_metric": name, "display_name": "Shared", "category": "Test", "unit": "USD",
         "direction_good": "up", "aggregation_type": "flow"} for name in ("A", "B")]})
    assert response.status_code == 200, response.text
    book = _book(client, period="2025-Q4", granularity="quarter")
    assert [(r["Part"].value, r["Actual"].value) for r in _table(book["Contributors"])] == [
        ("South", 20), ("North", 10), ("West", 4), ("East", 2)]


@pytest.mark.parametrize("granularity,period,prior", [("quarter", "2025-Q4", "2025-09"), ("year", "2025", "2024-12")])
def test_rollup_prior_period_comes_from_shared_facts(client, granularity, period, prior):  # noqa: F811
    _import(client, f"period,metric,value,budget\n{prior},Revenue,100,90\n2025-12,Revenue,130,110\n",
            {**LONG, "budget_col": "budget"})
    fact = client.get("/facts", params={"period": period, "granularity": granularity}).json()[0]
    row = _table(_book(client, period=period, granularity=granularity)["KPIs"])[0]
    assert row["Prior period"].value == 100
    assert row["Prior period"].value == fact["prior_value"]
    assert row["Plan"].value == 110


def test_export_names_the_dataset_as_the_app_shows_it():
    from app.close_export import content_disposition, display_name

    assert display_name("Opex_by-department.CSV") == "Opex by department"
    assert display_name("Q1 Actuals") == "Q1 Actuals"
    assert 'filename="CloseBrief - Opex by department - March 2026 close.xlsx"' in content_disposition(
        "Opex by department.csv", "2026-03")
