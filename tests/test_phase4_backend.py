"""Contributors, forecast corrections and fiscal YTD retain honest monthly figures."""

import pytest
from test_datasets import client  # noqa: F401
from test_import_honesty import LONG, _import, _upload

from app.db import get_connection
from app.scheduling import record_digest_run

DIM = {**LONG, "dimension_cols": ["Department", "Region"], "budget_col": "budget", "forecast_col": "forecast"}
DATA = ("period,metric,value,budget,forecast,Department,Region\n"
        "2025-01,Revenue,20,30,25,Sales,East\n2025-01,Revenue,80,60,70,Engineering,West\n"
        "2025-02,Revenue,40,30,35,Sales,East\n2025-02,Revenue,60,50,55,Engineering,West\n"
        "2025-03,Revenue,50,40,45,Sales,East\n2025-03,Revenue,50,50,55,Engineering,West\n")


def _facts(api, period, granularity="month"):
    response = api.get("/facts", params={"period": period, "granularity": granularity, "include_charts": False})
    assert response.status_code == 200, response.text
    return {r["metric"]: r for r in response.json()}


def _contributors(api, period="2025-01", granularity="month", metric="Revenue"):
    response = api.get("/kpis/contributors", params={"metric": metric, "period": period, "granularity": granularity})
    assert response.status_code == 200, response.text
    return response.json()


def _configure(api, name="Revenue", agg="flow", unit="USD"):
    response = api.post("/kpis", json={"kpis": [{"source_metric": name, "display_name": name,
        "category": "Test", "unit": unit, "direction_good": "up", "aggregation_type": agg}]})
    assert response.status_code == 200, response.text


def _join(api, ds, text, field="budget", dims=None):
    upload = _upload(api, text)
    response = api.post(f"/ingest/{upload}/join-budget", json={"target_dataset_id": ds, "field": field,
        "mapping": {**LONG, "dimension_cols": dims or []}})
    assert response.status_code == 200, response.text
    return response.json()


def _lock(api, ds, month="2025-01"):
    conn = get_connection()
    try:
        record_digest_run(conn, ds, month, 5, [], 0, "manual")
    finally:
        conn.close()
    response = api.post("/close/lock", json={"period": month})
    assert response.status_code == 200, response.text


def test_breakdown_first_dimension_sums_and_month_variance(client):  # noqa: F811
    ds = _import(client, DATA, DIM)["dataset_id"]
    result = _contributors(client)
    assert result["available"] and result["dimension"] == "Department" and result["reason"] is None
    assert result["aggregation_type"] == "flow"
    assert result["total"] == {"value": 100, "budget": 90, "var_abs": 10}
    assert result["members"] == [
        {"member": "Engineering", "value": 80, "budget": 60, "forecast": 70,
         "var_abs": 20, "var_pct": pytest.approx(100 / 3), "share_of_variance": 2},
        {"member": "Sales", "value": 20, "budget": 30, "forecast": 25,
         "var_abs": -10, "var_pct": pytest.approx(-100 / 3), "share_of_variance": -1}]
    assert _facts(client, "2025-01")["Revenue"]["has_breakdown"]
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM metric_breakdowns").fetchall()
        assert len(rows) == 6 and {r["dimension"] for r in rows} == {"Department"}
        assert sum(r["value"] for r in rows) == 300
        assert all(r["source_rows"] == 1 for r in rows)
    finally:
        conn.close()
    assert client.delete(f"/datasets/{ds}").status_code == 204


def test_contributors_quarter_flow_balance_and_ratio(client):  # noqa: F811
    _import(client, DATA, DIM)
    result = _contributors(client, "2025-Q1", "quarter")
    assert result["total"] == {"value": 300, "budget": 260, "var_abs": 40}
    assert [(r["member"], r["value"], r["budget"]) for r in result["members"]] == [
        ("Engineering", 190, 160), ("Sales", 110, 100)]
    _configure(client, agg="balance")
    result = _contributors(client, "2025-Q1", "quarter")
    assert result["total"] == {"value": 100, "budget": 90, "var_abs": 10}
    assert {r["value"] for r in result["members"]} == {50}
    _configure(client, agg="ratio", unit="%")
    result = _contributors(client)
    assert result["available"] is False
    assert result["reason"] == "A percentage can't be split into contributors."


def test_no_breakdown_single_member_and_blank_members(client):  # noqa: F811
    _import(client, "period,metric,value\n2025-01,Revenue,10\n")
    result = _contributors(client)
    assert not result["available"] and result["reason"] == "This KPI was imported without a breakdown column."
    _import(client, "period,metric,value,Department\n2025-01,Revenue,10,Sales\n2025-01,Revenue,20,Sales\n",
            {**LONG, "dimension_cols": ["Department"]})
    assert not _contributors(client)["available"]
    _import(client, "period,metric,value,Department\n2025-01,Revenue,10,\n2025-01,Revenue,20,Sales\n",
            {**LONG, "dimension_cols": ["Department"]})
    result = _contributors(client)
    assert {r["member"] for r in result["members"]} == {"(blank)", "Sales"}
    assert all(r["var_abs"] is None and r["share_of_variance"] is None for r in result["members"])
    assert result["members"][0]["value"] == 20


def test_forecast_import_rollup_and_join_change_log(client):  # noqa: F811
    ds = _import(client, DATA, DIM)["dataset_id"]
    fact = _facts(client, "2025-01")["Revenue"]
    assert fact["forecast"] == 95
    assert fact["deltas"]["forecast_var_abs"] == 5
    assert fact["deltas"]["forecast_var_pct"] == pytest.approx(5 / 95 * 100)
    quarter = _facts(client, "2025-Q1", "quarter")["Revenue"]
    assert quarter["forecast"] == 285 and quarter["deltas"]["forecast_var_abs"] == 15
    _join(client, ds, "period,metric,value,Department\n2025-01,Revenue,80,Sales\n2025-01,Revenue,30,Engineering\n",
          "forecast", ["Department"])
    fact = _facts(client, "2025-01")["Revenue"]
    assert fact["forecast"] == 110 and fact["deltas"]["forecast_var_abs"] == -10
    changes = client.get("/close/changes?period=2025-01").json()["changes"]
    assert [(r["field"], r["source"], r["old_value"], r["new_value"]) for r in changes] == [
        ("forecast", "forecast_join", 95, 110)]
    result = _contributors(client)
    assert {r["member"]: r["forecast"] for r in result["members"]} == {"Sales": 80, "Engineering": 30}
    receipt = client.get(f"/datasets/{ds}/receipts").json()[0]
    assert receipt["kind"] == "forecast_join"


def test_dimension_join_budgets_and_no_dimension_does_not_invent_allocations(client):  # noqa: F811
    ds = _import(client, DATA, DIM)["dataset_id"]
    _join(client, ds, "period,metric,value,Department\n2025-01,Revenue,10,Sales\n2025-01,Revenue,70,Engineering\n",
          dims=["Department"])
    result = _contributors(client)
    assert {r["member"]: r["budget"] for r in result["members"]} == {"Sales": 10, "Engineering": 70}
    _join(client, ds, "period,metric,value\n2025-01,Revenue,90\n")
    assert all(r["budget"] is None and r["var_abs"] is None for r in _contributors(client)["members"])


def test_locks_skip_breakdown_reimport_and_forecast_join(client):  # noqa: F811
    ds = _import(client, DATA, DIM)["dataset_id"]
    before = _contributors(client)
    _lock(client, ds)
    upload = _upload(client, DATA.replace(",20,30,25,", ",200,300,250,").replace(",40,30,35,", ",400,300,350,"))
    response = client.post(f"/ingest/{upload}/mapping?target_dataset_id={ds}", json=DIM)
    assert response.status_code == 200, response.text
    assert response.json()["import_report"]["locked_skipped"] == [{"period": "2025-01", "rows": 2}]
    assert _contributors(client) == before
    result = _join(client, ds, "period,metric,value\n2025-01,Revenue,999\n2025-02,Revenue,888\n", "forecast")
    assert result["locked_skipped"] == [{"period": "2025-01", "rows": 1}]
    assert _facts(client, "2025-01")["Revenue"]["forecast"] == 95
    assert _facts(client, "2025-02")["Revenue"]["forecast"] == 888
    assert all(r["period"] != "2025-01" for r in client.get("/close/changes?period=2025-Q1&granularity=quarter").json()["changes"])


@pytest.mark.parametrize("fiscal_start,period,prior", [(1, "2025-03", "2024-01"), (4, "2025-06", "2024-04")])
def test_ytd_flow_fiscal_span_budgets_and_prior(client, fiscal_start, period, prior):  # noqa: F811
    start = int(period[5:7]) - 2
    lines = ["period,metric,value,budget,forecast"]
    for i in range(3):
        lines += [f"2025-{start+i:02d},Revenue,{100+i*10},{90+i*10},{95+i*10}",
                  f"2024-{start+i:02d},Revenue,{50+i*10},40,45"]
    ds = _import(client, "\n".join(lines) + "\n", {**LONG, "budget_col": "budget", "forecast_col": "forecast"})["dataset_id"]
    conn = get_connection()
    try:
        conn.execute("UPDATE datasets SET fiscal_year_start=? WHERE id=?", (fiscal_start, ds))
        conn.commit()
    finally:
        conn.close()
    ytd = _facts(client, period)["Revenue"]["ytd"]
    assert ytd == {"months": 3, "value": 330, "budget": 300, "forecast": 315, "budget_months": 3,
                   "var_abs": 30, "var_pct": 10, "prior_value": 180, "yoy_pct": pytest.approx(150 / 180 * 100)}
    # A missing prior observation makes the whole comparison unavailable.
    conn = get_connection()
    try:
        conn.execute("DELETE FROM metric_values WHERE period=?", (prior,))
        conn.commit()
    finally:
        conn.close()
    assert _facts(client, period)["Revenue"]["ytd"]["prior_value"] is None
    assert _facts(client, period)["Revenue"]["ytd"]["yoy_pct"] is None


def test_ytd_missing_budget_balance_ratio_and_quarter_null(client):  # noqa: F811
    _import(client, "period,metric,value,budget,forecast\n2024-03,Cash,40,35,36\n"
            "2025-01,Cash,100,90,95\n2025-02,Cash,110,,105\n2025-03,Cash,120,115,118\n",
            {**LONG, "budget_col": "budget", "forecast_col": "forecast"})
    _configure(client, name="Cash", agg="flow")
    ytd = _facts(client, "2025-03")["Cash"]["ytd"]
    assert ytd["value"] == 330 and ytd["budget"] is None and ytd["budget_months"] == 2
    assert ytd["var_abs"] is None and ytd["var_pct"] is None
    _configure(client, name="Cash", agg="balance")
    ytd = _facts(client, "2025-03")["Cash"]["ytd"]
    assert (ytd["value"], ytd["budget"], ytd["forecast"], ytd["prior_value"], ytd["yoy_pct"]) == (120, 115, 118, 40, 200)
    _configure(client, name="Cash", agg="ratio", unit="%")
    assert _facts(client, "2025-03")["Cash"]["ytd"] is None
    assert _facts(client, "2025-Q1", "quarter")["Cash"]["ytd"] is None


@pytest.mark.parametrize("name", ["Forecast", "Fcst", "Outlook", "Latest Estimate", "LE", "Reforecast"])
def test_profiler_distinguishes_forecast_and_budget(client, name):  # noqa: F811
    upload = _upload(client, f"period,metric,value,budget,{name}\n2025-01,Revenue,100,90,95\n")
    result = client.get(f"/ingest/{upload}/schema").json()
    roles = {c["column_name"]: c["guessed_role"] for c in result["columns"]}
    assert roles[name] == "forecast" and roles["budget"] == "budget"
    assert result["suggested_mapping"]["forecast_col"] == name



def test_rollup_breakdown_link_requires_full_coverage_and_balance_last_month(client):  # noqa: F811
    ds = _import(client, DATA, DIM)["dataset_id"]
    conn = get_connection()
    try:
        conn.execute("DELETE FROM metric_breakdowns WHERE period='2025-03'")
        conn.commit()
    finally:
        conn.close()
    assert not _contributors(client, "2025-Q1", "quarter")["available"]
    assert not _facts(client, "2025-Q1", "quarter")["Revenue"]["has_breakdown"]
    _configure(client, agg="balance")
    assert not _facts(client, "2025-Q1", "quarter")["Revenue"]["has_breakdown"]
    assert not _contributors(client, "2025-Q1", "quarter")["available"]
    assert ds


def test_join_aliases_resolve_once_before_summing_member_forecasts(client):  # noqa: F811
    ds = _import(client, DATA, DIM)["dataset_id"]
    _join(client, ds, "period,metric,value,Department\n2025-01,Revenue,80,Sales\n2025-01,revenue,30,Engineering\n",
          "forecast", ["Department"])
    assert _facts(client, "2025-01")["Revenue"]["forecast"] == 110
    assert {r["member"]: r["forecast"] for r in _contributors(client)["members"]} == {"Sales": 80, "Engineering": 30}


def test_breakdown_replaced_removed_and_record_failure_rolls_back(client, monkeypatch):  # noqa: F811
    from app import close_control

    ds = _import(client, DATA, DIM)["dataset_id"]
    before = _contributors(client)
    upload = _upload(client, DATA.replace(",20,30,25,", ",200,300,250,"))
    original = close_control.record_changes
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("rollback breakdown and forecast")
    with monkeypatch.context() as patch:
        patch.setattr(close_control, "record_changes", fail)
        with pytest.raises(RuntimeError, match="rollback breakdown and forecast"):
            client.post(f"/ingest/{upload}/mapping?target_dataset_id={ds}", json=DIM)
    assert _contributors(client) == before
    upload = _upload(client, "period,metric,value,Department\n2025-01,Revenue,100,Sales\n")
    response = client.post(f"/ingest/{upload}/mapping?target_dataset_id={ds}",
                           json={**LONG, "dimension_cols": ["Department"]})
    assert response.status_code == 200, response.text
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM metric_breakdowns").fetchone()["n"] == 0
    finally:
        conn.close()


def test_forecast_percent_contract_zero_and_negative_denominators(client):  # noqa: F811
    _import(client, "period,metric,value,forecast\n2025-01,Margin,38,40\n2025-02,Margin,38,0\n2025-03,Margin,-30,-40\n",
            {**LONG, "forecast_col": "forecast"})
    _configure(client, name="Margin", agg="ratio", unit="%")
    deltas = _facts(client, "2025-01")["Margin"]["deltas"]
    assert deltas["forecast_var_abs"] == -2 and deltas["forecast_var_pct"] == -5
    assert _facts(client, "2025-02")["Margin"]["deltas"]["forecast_var_pct"] is None
    assert _facts(client, "2025-03")["Margin"]["deltas"]["forecast_var_pct"] == 25


@pytest.mark.sqlite_only
def test_legacy_schema_upgrade_preserves_checks_rows_indexes_and_sequence(client):  # noqa: F811
    from app import db

    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n")["dataset_id"]
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id=?", (ds,)).fetchone()["id"]
        conn.execute("DROP TABLE value_changes")
        conn.execute("""CREATE TABLE value_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id INTEGER,
            dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
            metric_id INTEGER NOT NULL REFERENCES metrics(id) ON DELETE CASCADE, period TEXT NOT NULL,
            field TEXT NOT NULL CHECK (field IN ('value', 'budget')), old_value REAL, new_value REAL,
            source TEXT NOT NULL CHECK (source IN ('import', 'budget_join', 'sync', 'custom_kpi')),
            actor_id TEXT, actor_email TEXT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("CREATE INDEX legacy_change_index ON value_changes(actor_email)")
        conn.execute("""INSERT INTO value_changes (id,dataset_id,metric_id,period,field,old_value,new_value,source)
            VALUES (70,?,?,'2025-01','value',90,100,'import')""", (ds, mid))
        conn.execute("DELETE FROM value_changes WHERE id=70")
        conn.execute("""INSERT INTO value_changes (id,dataset_id,metric_id,period,field,old_value,new_value,source)
            VALUES (7,?,?,'2025-01','value',80,90,'import')""", (ds, mid))
        conn.execute("DROP TABLE import_receipts")
        conn.execute("""CREATE TABLE import_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
            workspace_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            kind TEXT NOT NULL CHECK (kind IN ('import', 'budget_join', 'connector_sync')),
            filename TEXT NOT NULL, mapping TEXT NOT NULL, report TEXT NOT NULL, kpi_totals TEXT NOT NULL)""")
        conn.execute("ALTER TABLE metric_values DROP COLUMN forecast")
        conn.execute("ALTER TABLE computed_facts DROP COLUMN forecast_var_abs")
        conn.execute("ALTER TABLE computed_facts DROP COLUMN forecast_var_pct")
        conn.commit()
    finally:
        conn.close()
    db.init_db()
    db.init_db()
    conn = get_connection()
    try:
        assert conn.execute("SELECT old_value FROM value_changes WHERE id=7").fetchone()["old_value"] == 80
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='legacy_change_index'").fetchone()
        change = conn.execute("""INSERT INTO value_changes (dataset_id,metric_id,period,field,new_value,source)
            VALUES (?,?,'2025-01','forecast',95,'forecast_join')""", (ds, mid))
        assert change.lastrowid > 70
        conn.execute("""INSERT INTO import_receipts (dataset_id,kind,filename,mapping,report,kpi_totals)
            VALUES (?,'forecast_join','forecast.csv','{}','{}','[]')""", (ds,))
        assert conn.execute("SELECT forecast FROM metric_values").fetchone()["forecast"] is None
        conn.commit()
    finally:
        conn.close()



def test_wide_forecasts_participate_in_duplicates_and_validate_columns(client):  # noqa: F811
    mapping = {"layout": "wide", "wide_metric_col": "metric", "wide_period_cols": ["Jan-25"],
               "id_col": "id", "forecast_col": "outlook"}
    result = _import(client, "metric,id,Jan-25,outlook\nRevenue,A,100,90\nRevenue,A,100,95\n", mapping)
    assert result["import_report"]["duplicates_dropped"]["count"] == 0
    assert _facts(client, "2025-01")["Revenue"]["forecast"] == 185
    upload = _upload(client, "metric,id,Jan-25,outlook\nRevenue,A,100,no-number\n")
    for forecast in ("missing", "outlook"):
        response = client.post(f"/ingest/{upload}/mapping", json={**mapping, "forecast_col": forecast})
        assert response.status_code == 422, response.text



def test_derived_refresh_retains_joined_forecast(client):  # noqa: F811
    ds = _import(client, "period,metric,value\n2025-01,Revenue,100\n")["dataset_id"]
    response = client.post("/kpis/derived", json={"name": "Double", "formula": "[Revenue]*2"})
    assert response.status_code == 201, response.text
    _join(client, ds, "period,metric,value\n2025-01,Double,190\n", "forecast")
    assert _facts(client, "2025-01")["Double"]["forecast"] == 190
    changes = client.get("/close/changes?period=2025-01").json()["changes"]
    assert [(r["field"], r["source"], r["new_value"]) for r in changes if r["field"] == "forecast"] == [
        ("forecast", "forecast_join", 190)]


def test_fact_query_count_does_not_grow_with_kpi_count(client, monkeypatch):  # noqa: F811
    from app.routers import facts

    factory = facts.get_connection
    queries = []
    class Counted:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, sql, params=()):
            if sql.lstrip().upper().startswith("SELECT"):
                queries.append(sql)
            return self.conn.execute(sql, params)
        def __getattr__(self, name):
            return getattr(self.conn, name)
    monkeypatch.setattr(facts, "get_connection", lambda: Counted(factory()))
    _import(client, "period,metric,value\n2025-01,Revenue,100\n")
    _facts(client, "2025-01")
    monthly_count = len(queries)
    queries.clear()
    _facts(client, "2025-Q1", "quarter")
    quarter_count = len(queries)
    _import(client, "period,metric,value\n" + "".join(f"2025-01,Metric {i},{i+1}\n" for i in range(35)))
    queries.clear()
    assert len(_facts(client, "2025-01")) == 35
    assert len(queries) == monthly_count
    queries.clear()
    assert len(_facts(client, "2025-Q1", "quarter")) == 35
    assert len(queries) == quarter_count
