"""Per-dataset currency is metadata; amounts, units and deltas stay unchanged."""

import pytest
from test_auth import _bearer
from test_auth import make_client as make_client
from test_datasets import client as client

import app.routers.facts as route_facts
from app.generation.guard import check_faithfulness, extract_numbers, faithfulness_messages
from app.schemas import ComputedFact

CSV = "period,metric,value,budget\n2025-01,Revenue,4000000,3900000\n2025-02,Revenue,4200000,4100000\n"
MAPPING = {"layout": "long", "period_col": "period", "metric_col": "metric",
           "value_col": "value", "budget_col": "budget"}


def _import(client, currency=None, flexible=False):
    data = {} if currency is None else {"currency": currency}
    response = client.post("/ingest/upload" if flexible else "/ingest",
                           files={"file": ("amounts.csv", CSV, "text/csv")}, data=data)
    assert response.status_code == 200, response.text
    if flexible:
        response = client.post(f"/ingest/{response.json()['upload_id']}/mapping", json=MAPPING)
        assert response.status_code == 200, response.text
    return client.get("/datasets").json()["active_id"]


@pytest.mark.parametrize("flexible", [False, True])
def test_import_defaults_to_usd(client, flexible):
    ds = _import(client, flexible=flexible)
    row = next(d for d in client.get("/datasets").json()["datasets"] if d["id"] == ds)
    assert row["currency"] == "USD"


@pytest.mark.parametrize("flexible", [False, True])
def test_import_and_patch_currency_reaches_amount_responses(client, flexible):
    ds = _import(client, "INR", flexible)
    assert client.get("/datasets").json()["datasets"][0]["currency"] == "INR"
    before = client.get("/facts?period=2025-02&include_charts=false").json()[0]
    assert before["currency"] == "INR"
    assert client.patch(f"/datasets/{ds}", json={"currency": "EUR"}).json()["currency"] == "EUR"
    assert client.get("/datasets").json()["datasets"][0]["currency"] == "EUR"
    assert client.get("/datasets").json()["currency"] == "EUR"
    assert client.get("/domain").json()["currency"] == "EUR"
    client.post("/kpis", json={"kpis": [
        {"source_metric": "Revenue", "display_name": "Revenue", "unit": "USD"},
        {"source_metric": "No data", "display_name": "No data", "unit": "count"},
    ]}).raise_for_status()
    facts = client.get("/facts?period=2025-02&include_charts=false").json()
    assert {f["currency"] for f in facts} == {"EUR"}
    revenue = next(f for f in facts if f["metric"] == "Revenue")
    assert (revenue["value"], revenue["unit"], revenue["deltas"]) == (before["value"], before["unit"], before["deltas"])
    assert {f["currency"] for f in client.get("/facts?period=2025-Q1&granularity=quarter").json()} == {"EUR"}
    kpis = client.get("/kpis").json()
    assert kpis["currency"] == "EUR"
    assert {k["currency"] for k in kpis["selected"]} == {"EUR"}
    assert client.get("/forecast?metric=Revenue").json()["currency"] == "EUR"


@pytest.mark.parametrize("code", ["XYZ", "usd", "", None, 42, ["USD"]])
def test_patch_rejects_invalid_currency(client, code):
    ds = _import(client)
    assert client.patch(f"/datasets/{ds}", json={"currency": code}).status_code == 422
    assert client.get("/datasets").json()["datasets"][0]["currency"] == "USD"


@pytest.mark.parametrize("path", ["/ingest", "/ingest/upload"])
def test_import_rejects_invalid_currency(client, path):
    response = client.post(path, files={"file": ("amounts.csv", CSV, "text/csv")}, data={"currency": "XYZ"})
    assert response.status_code == 422
    assert client.get("/datasets").json()["datasets"] == []


@pytest.mark.parametrize("role", ["viewer", "executive", "analyst", "admin"])
@pytest.mark.sqlite_only
def test_patch_requires_existing_write_permission(make_client, role):
    c = make_client(auth_on=True, seed_roles={"u1": ("u1@co.com", role)})
    # Provision the real user's private workspace, then seed its dataset directly.
    c.get("/me", headers=_bearer())
    from app import datasets, workspaces
    from app.db import get_connection

    conn = get_connection()
    try:
        ws = workspaces.ensure_user_workspace(conn, "u1", "u1@co.com")
        ds = datasets.create_dataset(conn, "Private", workspace_id=ws, activate=False)
    finally:
        conn.close()
    response = c.patch(f"/datasets/{ds}", json={"currency": "EUR"}, headers=_bearer())
    assert response.status_code == (200 if role in ("analyst", "admin") else 403)


@pytest.mark.sqlite_only
def test_patch_another_workspace_dataset_is_404(make_client):
    c = make_client(auth_on=True)
    from app import datasets, workspaces
    from app.db import get_connection

    conn = get_connection()
    try:
        ws = workspaces.create_workspace(conn, "Someone else's", "other")
        ds = datasets.create_dataset(conn, "Private", workspace_id=ws, activate=False)
    finally:
        conn.close()
    response = c.patch(f"/datasets/{ds}", json={"currency": "EUR"}, headers=_bearer())
    assert response.status_code == 404


@pytest.mark.parametrize("symbol", ["$", "\u20ac", "\u20b9", "\u00a3", "\u00a5"])
@pytest.mark.parametrize("text,passed", [
    ("Revenue fell to {s}4.2M.", True),
    ("Revenue rose to {s}4.2M.", False),
    ("Revenue was {s}4.28M.", False),
    ("Revenue missed plan by {s}370K.", True),
    ("Revenue was +{s}370K ahead of plan.", False),
    ("Revenue was {s}2025.", False),
    ("Revenue was {s}12 months.", False),
])
def test_currency_guard_keeps_dollar_precision_and_direction(symbol, text, passed):
    fact = ComputedFact(metric="Revenue", period="2025-03", value=4200000, prior_value=4772727,
                        deltas={"mom_pct": -12, "budget_var_abs": -370000})
    narrative = text.format(s=symbol)
    assert check_faithfulness(narrative, fact)[0] is passed
    assert bool(faithfulness_messages(narrative, fact)) is not passed
    assert extract_numbers(f"{symbol}4.2M") == [(4200000, False)]


@pytest.mark.parametrize("code", ["USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD", "SGD", "CHF"])
def test_patch_accepts_supported_iso_codes(client, code):
    ds = _import(client)
    response = client.patch(f"/datasets/{ds}", json={"currency": code})
    assert response.status_code == 200
    assert response.json()["currency"] == code


@pytest.mark.sqlite_only
def test_legacy_sqlite_upgrade_defaults_existing_rows_to_usd(tmp_path, monkeypatch):
    import sqlite3

    from app import db
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "legacy.db"))
    old = sqlite3.connect(settings.db_path)
    old.executescript("""
        CREATE TABLE datasets (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        INSERT INTO datasets (id, name) VALUES (1, 'Existing');
        CREATE TABLE uploads (id TEXT PRIMARY KEY, filename TEXT NOT NULL, file_path TEXT);
        INSERT INTO uploads (id, filename) VALUES ('old', 'old.csv');
    """)
    old.close()
    db.init_db()
    db.init_db()
    conn = db.get_connection()
    try:
        for table in ("datasets", "uploads"):
            assert conn.execute(f"SELECT currency FROM {table}").fetchone()["currency"] == "USD"
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f"UPDATE {table} SET currency = NULL")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f"UPDATE {table} SET currency = 'XYZ'")
    finally:
        conn.close()


def test_loaded_fact_and_facts_only_response_preserve_currency(client):
    from app.db import get_connection
    from app.generation.generate import _facts_only_output

    _import(client, "EUR")
    conn = get_connection()
    try:
        fact = route_facts._load_computed_fact(conn, "Revenue", "2025-02")
        assert fact.currency == "EUR"
        assert _facts_only_output(fact).model_dump()["currency"] == "EUR"
    finally:
        conn.close()


def test_analytics_amount_responses_preserve_currency(client, monkeypatch):
    from types import SimpleNamespace

    from app.routers import insights

    _import(client, "GBP")
    scenario = client.post("/scenario", json={"metric": "Revenue", "period": "2025-02", "price_pct": 10})
    assert scenario.status_code == 200
    assert scenario.json()["currency"] == "GBP"
    root_cause = client.get("/insights/root-cause?metric=Revenue&period=2025-02")
    assert root_cause.status_code == 200
    assert root_cause.json()["currency"] == "GBP"
    # A local fake supplies prose only; no external client/network is invoked.
    usage = SimpleNamespace(prompt_tokens=0, completion_tokens=0, cost_usd=0)
    fake = SimpleNamespace(generate_narrative=lambda *args: (SimpleNamespace(narrative="Offline test."), usage))
    monkeypatch.setattr(insights, "get_llm_client", lambda: fake)
    narrative = client.post("/forecast/narrative", json={"metric": "Revenue"})
    assert narrative.status_code == 200
    assert narrative.json()["currency"] == "GBP"


def test_review_queue_dataset_metadata_preserves_currency(client):
    from app.auth import ANONYMOUS_ADMIN
    from app.db import get_connection

    ds = _import(client, "CHF")
    conn = get_connection()
    try:
        mid = conn.execute("SELECT id FROM metrics WHERE dataset_id = ?", (ds,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO generated_reports "
            "(metric_id, period, confidence, faithfulness, review_status, assigned_email) "
            "VALUES (?, '2025-02', 'High', 'passed', 'pending', ?)", (mid, ANONYMOUS_ADMIN.email))
        conn.commit()
    finally:
        conn.close()
    response = client.get("/reports/review-queue?all_datasets=true")
    assert response.status_code == 200
    assert response.json()[0]["currency"] == "CHF"
