"""Portfolio safety: anyone can sign in, so AI spend is capped per visitor per UTC
day and in total per day; the owner (admin) is exempt."""

import pytest
from dbharness import use_test_db
from fastapi import HTTPException

from app.auth import CurrentUser

VISITOR = CurrentUser(id="11111111-1111-1111-1111-111111111111", email="v@x.com", role="analyst")
OTHER = CurrentUser(id="22222222-2222-2222-2222-222222222222", email="o@x.com", role="analyst")
OWNER = CurrentUser(id="33333333-3333-3333-3333-333333333333", email="me@x.com", role="admin")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    import app.main as main
    from app.config import settings
    from app.db import get_connection

    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "user_daily_ai_budget_usd", 0.50)
    monkeypatch.setattr(settings, "global_daily_ai_budget_usd", 5.00)
    main.init_db()
    c = get_connection()
    yield c
    c.close()


def _spend(conn, user, amount, when=None):
    if when:
        conn.execute("INSERT INTO llm_calls (endpoint, cost_usd, user_id, created_at) VALUES ('/x', ?, ?, ?)",
                     (amount, user.id, when))
    else:
        from app.services import log_llm_call
        log_llm_call("/x", None, 0, 0, amount, 0, user.id)
    conn.commit()


def test_visitor_is_stopped_at_their_daily_allowance(conn):
    from app.services import enforce_daily_caps
    _spend(conn, VISITOR, 0.49)
    enforce_daily_caps(conn, VISITOR)                       # under the cap: allowed
    _spend(conn, VISITOR, 0.02)
    with pytest.raises(HTTPException) as e:
        enforce_daily_caps(conn, VISITOR)
    assert e.value.status_code == 429 and "resets at midnight UTC" in e.value.detail
    enforce_daily_caps(conn, OTHER)                          # another visitor is unaffected


def test_yesterdays_spend_does_not_count(conn):
    from app.services import enforce_daily_caps
    _spend(conn, VISITOR, 3.00, when="2000-01-01 12:00:00")
    enforce_daily_caps(conn, VISITOR)


def test_overall_cap_stops_everyone_but_the_owner(conn):
    from app.services import enforce_daily_caps
    _spend(conn, OTHER, 5.00)
    with pytest.raises(HTTPException) as e:
        enforce_daily_caps(conn, VISITOR)
    assert e.value.status_code == 429 and "for this demo" in e.value.detail
    enforce_daily_caps(conn, OWNER)                          # the owner is exempt


def test_zero_disables_a_cap(conn, monkeypatch):
    from app.config import settings
    from app.services import enforce_daily_caps
    monkeypatch.setattr(settings, "user_daily_ai_budget_usd", 0)
    monkeypatch.setattr(settings, "global_daily_ai_budget_usd", 0)
    _spend(conn, VISITOR, 50.0)
    enforce_daily_caps(conn, VISITOR)


def test_new_google_users_get_a_working_sandbox_by_default(monkeypatch):
    from app.config import Settings
    monkeypatch.delenv("GOOGLE_DEFAULT_ROLE", raising=False)
    assert Settings(_env_file=None).google_default_role == "analyst"


def test_two_workspaces_contribute_to_global_cap_without_changing_user_cap(conn, monkeypatch):
    from app import datasets, services, workspaces
    from app.config import settings
    from app.db import get_connection

    first = workspaces.create_workspace(conn, "First", VISITOR.id)
    second = workspaces.create_workspace(conn, "Second", OTHER.id)
    scope = datasets._workspace_scope.set(None)
    try:
        for ws, user, amount in ((first, VISITOR, 0.30), (second, OTHER, 4.70)):
            datasets.set_workspace_scope(ws)
            services.log_llm_call("/offline-test", None, 0, 0, amount, 0, user.id)
        datasets.set_workspace_scope(first)
        scoped = get_connection()
        try:
            assert services._spent_today(scoped) == pytest.approx(5.0)
            with pytest.raises(HTTPException) as error:
                services.enforce_daily_caps(scoped, VISITOR)
            assert error.value.status_code == 429 and "for this demo" in error.value.detail
            monkeypatch.setattr(settings, "global_daily_ai_budget_usd", 0)
            services.enforce_daily_caps(scoped, VISITOR)  # own spend is still only 0.30
        finally:
            scoped.close()
        datasets.set_workspace_scope(second)
        scoped = get_connection()
        try:
            with pytest.raises(HTTPException) as error:
                services.enforce_daily_caps(scoped, OTHER)
            assert error.value.status_code == 429 and "You've used" in error.value.detail
        finally:
            scoped.close()
    finally:
        datasets._workspace_scope.reset(scope)


@pytest.mark.sqlite_only
def test_daily_spend_and_call_insert_are_atomic(conn):
    from app import services

    conn.execute("CREATE TRIGGER reject_daily BEFORE INSERT ON ai_spend_daily "
                 "BEGIN SELECT RAISE(ABORT, 'simulated total failure'); END")
    conn.commit()
    services.log_llm_call("/offline-test", None, 0, 0, 0.25, 0, VISITOR.id)
    assert conn.execute("SELECT count(*) AS n FROM llm_calls").fetchone()["n"] == 0
    assert services._spent_today(conn) == 0.0


@pytest.mark.sqlite_only
def test_sqlite_daily_spend_backfill_preserves_total_after_retention(tmp_path, monkeypatch):
    import sqlite3

    from app import db, services
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "before-global-totals.db"))
    legacy = sqlite3.connect(settings.db_path)
    legacy.executescript("""
        CREATE TABLE llm_calls (id INTEGER PRIMARY KEY, endpoint TEXT, cost_usd REAL,
                                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO llm_calls (cost_usd) VALUES (2.5), (NULL);
        INSERT INTO llm_calls (cost_usd, created_at) VALUES (99, '2000-01-01 00:00:00');
    """)
    legacy.close()
    db.init_db()
    conn = db.get_connection()
    try:
        assert services._spent_today(conn) == pytest.approx(2.5)
        conn.execute("DELETE FROM llm_calls")
        conn.commit()
    finally:
        conn.close()
    db.init_db()
    conn = db.get_connection()
    try:
        assert services._spent_today(conn) == pytest.approx(2.5)
    finally:
        conn.close()
