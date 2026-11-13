"""Demo mode (v3.0): isolated read-only workspace. Demo sessions authenticate
as a viewer via the X-Closebrief-Demo header, can only read, and can never see
or touch real datasets."""

import io

import pytest
from dbharness import use_test_db

import app.routers.facts as route_facts

DEMO = {"X-Closebrief-Demo": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main
    from app.config import settings

    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "demo.db"))
    monkeypatch.setattr(settings, "vector_backend", "faiss")
    monkeypatch.setattr(settings, "embedding_provider", "offline")
    # Auth ACTIVE (anonymous is 401) + demo mode on.
    monkeypatch.setattr(settings, "supabase_url", "https://demo.supabase.co")
    monkeypatch.setattr(settings, "supabase_jwt_secret", "x" * 32)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", True)

    from app.deps import shared_cache, shared_embedder, shared_vector_store

    shared_cache.cache_clear()
    shared_embedder.cache_clear()
    shared_vector_store.cache_clear()

    main.init_db()
    # A "real" dataset the demo must never see, plus the seeded demo dataset.
    conn = main.get_connection()
    from app.datasets import create_dataset

    create_dataset(conn, "REAL secret upload", is_demo=False)
    conn.close()
    from app.demo import seed_demo

    conn = main.get_connection()
    seed_demo(conn)
    conn.close()

    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c


def test_demo_sees_only_demo_datasets(client):
    r = client.get("/datasets", headers=DEMO)
    assert r.status_code == 200
    names = {d["name"] for d in r.json()["datasets"]}
    assert names == {"Demo — Sample FP&A", "Demo — Marketing Funnel"}
    assert "REAL secret upload" not in names


def test_demo_can_switch_datasets_and_see_funnel(client):
    # A viewer may switch between demo datasets (read-only navigation)...
    ds = client.get("/datasets", headers=DEMO).json()["datasets"]
    mkt = next(d for d in ds if d["name"] == "Demo — Marketing Funnel")
    assert client.post(f"/datasets/{mkt['id']}/activate", headers=DEMO).status_code == 200
    # ...and, sending that choice with each request, sees the acquisition funnel.
    mine = {**DEMO, "X-Dataset-Id": str(mkt["id"])}
    dom = client.get("/domain", headers=mine).json()
    assert dom["slug"] == "marketing" and dom["funnel"]
    periods = client.get("/periods", headers=mine).json()
    r = client.get(f"/funnel?period={periods[-1]}", headers=mine)
    assert r.status_code == 200
    stages = [s["name"] for s in r.json()["stages"]]
    assert stages == ["Impressions", "Clicks", "Signups", "Conversions"]


def test_one_visitors_switch_does_not_change_what_others_see(client):
    # S2: a read-only visitor's switch is theirs alone; the shared default is untouched.
    ds = client.get("/datasets", headers=DEMO).json()
    default = ds["active_id"]
    other = next(d["id"] for d in ds["datasets"] if d["id"] != default)
    assert client.post(f"/datasets/{other}/activate", headers=DEMO).status_code == 200
    assert client.get("/datasets", headers=DEMO).json()["active_id"] == default
    mine = client.get("/datasets", headers={**DEMO, "X-Dataset-Id": str(other)}).json()
    assert mine["active_id"] == other
    assert [d["id"] for d in mine["datasets"] if d["is_active"]] == [other]


def test_a_dataset_header_cannot_reach_another_universe(client):
    # The real dataset (id 1) is invisible to a demo session even when asked for by id.
    r = client.get("/datasets", headers={**DEMO, "X-Dataset-Id": "1"}).json()
    assert r["active_id"] != 1 and all(d["id"] != 1 for d in r["datasets"])


def test_demo_cannot_activate_real_dataset(client):
    # The scoped activate check blocks a viewer from flipping is_active on a real
    # dataset (id 1 is 'REAL secret upload' created before seeding).
    assert client.post("/datasets/1/activate", headers=DEMO).status_code == 404


def test_demo_writes_are_forbidden(client):
    # Every write path the visitor might hit must 403 (role guard), not 200.
    assert (
        client.post(
            "/context",
            headers=DEMO,
            json={"type": "event_note", "title": "x", "body": "y", "metric_tags": [], "effective_date": "2025-01"},
        ).status_code
        == 403
    )
    assert client.request("DELETE", "/datasets/1", headers=DEMO).status_code == 403
    assert (
        client.post(
            "/ingest",
            headers=DEMO,
            files={"file": ("x.csv", io.BytesIO(b"period,metric,value,budget\n"), "text/csv")},
        ).status_code
        == 403
    )
    assert client.post("/kpis", headers=DEMO, json={"kpis": []}).status_code == 403


def test_demo_can_read_and_ask(client):
    assert client.get("/periods", headers=DEMO).status_code == 200
    assert client.get("/facts?period=2025-01", headers=DEMO).status_code == 200
    assert client.get("/context", headers=DEMO).status_code == 200


def test_no_demo_header_still_401(client):
    # Without the demo opt-in header, an anonymous request is rejected.
    assert client.get("/facts?period=2025-01").status_code == 401


def test_seed_demo_idempotent(client):
    import app.main as main
    from app.demo import DEMO_DATASET_NAME, seed_demo

    conn = main.get_connection()
    assert seed_demo(conn) is False  # both already seeded by the fixture
    n = conn.execute("SELECT COUNT(*) AS n FROM datasets WHERE name = ?", (DEMO_DATASET_NAME,)).fetchone()["n"]
    conn.close()
    assert n == 1


def test_seeded_closes_have_two_shared_months_and_one_in_progress(client):
    import app.main as main
    from app.demo import seed_demo
    from app.scheduling import list_digest_runs

    conn = main.get_connection()
    for ds in conn.execute("SELECT id FROM datasets WHERE is_demo = true").fetchall():
        periods = [
            r["period"]
            for r in conn.execute(
                "SELECT DISTINCT cf.period FROM computed_facts cf JOIN metrics m ON m.id=cf.metric_id "
                "WHERE m.dataset_id=? ORDER BY cf.period DESC LIMIT 3",
                (ds["id"],),
            ).fetchall()
        ]
        for i, period in enumerate(periods):
            reports = conn.execute(
                "SELECT gr.* FROM generated_reports gr JOIN metrics m ON m.id=gr.metric_id WHERE m.dataset_id=? AND gr.period=?",
                (ds["id"], period),
            ).fetchall()
            assert reports
            approved = sum(r["review_status"] == "approved" for r in reports)
            assert (0 < approved < len(reports)) if i == 0 else approved == len(reports)
        runs = list_digest_runs(conn, ds["id"])
        assert {r["period"] for r in runs} == set(periods[1:])
        assert all(r["trigger"] == "scheduled" and len(r["items"]) == 3 for r in runs)
        assert all(
            {"metric", "headline", "detail", "budget_var_abs", "budget_var_pct", "faithfulness"} <= item.keys()
            and item["faithfulness"] == "passed"
            for r in runs
            for item in r["items"]
        )
    before = conn.execute("SELECT COUNT(*) AS n FROM digest_runs").fetchone()["n"]
    assert seed_demo(conn) is False
    assert conn.execute("SELECT COUNT(*) AS n FROM digest_runs").fetchone()["n"] == before
    conn.close()


@pytest.mark.parametrize(
    "value,unit,signed,want",
    [
        (102400, "USD", False, "$102K"),
        (1420000, "USD", False, "$1.42M"),
        (-370000, "USD", False, "−$370K"),
        (12.5, "USD", False, "$12.5"),
        (0, "USD", False, "$0"),
        (-5.64, "%", True, "−5.6%"),
        (5.64, "%", True, "+5.6%"),
        (5, "%", False, "5.0%"),
        (1234.5, "count", False, "1,235"),
        (-1234.5, "count", False, "−1,234"),
    ],
)
def test_demo_figure_formatter_matches_figures_panel(value, unit, signed, want):
    from app.demo import format_figure

    assert format_figure(value, unit, signed=signed) == want


def test_every_seeded_demo_narrative_passes_faithfulness(client):
    import app.main as main
    from app.generation.guard import check_faithfulness

    conn = main.get_connection()
    reports = conn.execute(
        "SELECT gr.* FROM generated_reports gr JOIN metrics m ON m.id=gr.metric_id JOIN datasets d ON d.id=m.dataset_id WHERE d.is_demo=true"
    ).fetchall()
    assert len(reports) > 20
    for r in reports:
        fact = route_facts._fact_by_metric_id(conn, r["metric_id"], r["period"])
        passed, bad = check_faithfulness(r["narrative"], fact)
        assert passed, (fact.metric, r["period"], r["narrative"], bad)
        assert len(r["narrative"].split()) < 90
        assert "No reason recorded" not in r["narrative"] and "The note" not in r["narrative"]
        assert "vs plan" in r["narrative"] or "vs last month" in r["narrative"]
    conn.close()


@pytest.mark.parametrize(
    "period,budget,mom,cited",
    [
        ("2025-06", 5.6, 4.0, True),
        ("2025-02", -5.6, -4.0, False),
        ("2025-06", None, -5.6, False),
    ],
)
def test_template_uses_only_relevant_cited_reasons_and_real_comparisons(client, period, budget, mom, cited):
    import json
    from types import SimpleNamespace

    import app.main as main
    from app.datasets import create_dataset
    from app.demo import _seed_narratives
    from app.generation.guard import check_faithfulness
    from app.schemas import ContextSnippet

    conn = main.get_connection()
    ds = create_dataset(conn, "Template regression")
    mid = conn.execute("INSERT INTO metrics (dataset_id,name) VALUES (?, 'Net Revenue')", (ds,)).lastrowid
    conn.execute(
        "INSERT INTO computed_facts (metric_id,period,value,mom_pct,budget_var_pct) VALUES (?,?,1420000,?,?)",
        (mid, period, mom, budget),
    )
    doc = SimpleNamespace(
        id=999,
        type="event_note",
        title="March 2025 enterprise pricing change",
        body="Enterprise prices increased in March.",
        effective_date="2025-03",
        metric_tags=["Net Revenue"],
    )
    _seed_narratives(conn, ds, [doc])
    report = conn.execute("SELECT * FROM generated_reports WHERE metric_id=?", (mid,)).fetchone()
    text = report["narrative"]
    assert len(text.split()) < 90
    assert "$1.42M" in text
    assert (
        ("vs last month" in text and "No budget comparison is available." in text)
        if budget is None
        else "vs plan" in text
    )
    assert bool(json.loads(report["sources"])) is cited
    assert ("price increase" in text) is cited
    assert doc.title not in text and "No reason recorded" not in text
    assert check_faithfulness(
        text, route_facts._fact_by_metric_id(conn, mid, period), [ContextSnippet(id="ctx_999", body=doc.body)]
    )[0]
    conn.close()
