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
        conn.execute("INSERT INTO llm_calls (endpoint, cost_usd, user_id) VALUES ('/x', ?, ?)", (amount, user.id))
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
