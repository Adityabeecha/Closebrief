"""S9: with sign-in on, a draft may only cite notes retrieved from the caller's own
Context Library; context sent in the request body is ignored."""

import pytest

from app.auth import CurrentUser
from app.schemas import ComputedFact, ContextSnippet, GenerateInsightRequest

USER = CurrentUser(id="11111111-1111-1111-1111-111111111111", email="v@x.com", role="analyst")
FACT = ComputedFact(metric="Net Revenue", category="Revenue", period="2025-03", value=4200000,
                    prior_value=4772727, unit="USD",
                    deltas={"mom_pct": -12.0, "yoy_pct": 4.5, "budget_var_abs": -370000, "budget_var_pct": -8.1},
                    trend="up", is_anomaly=True)
FAKE = ContextSnippet(id="ctx_999", type="event_note", title="Invented", body="A made-up reason.")


class _Stop(Exception):
    pass


@pytest.fixture
def seen(monkeypatch):
    import app.main as main

    class _Conn:
        def close(self):
            pass

    got = {}

    def _generate(fact, context, *a, **k):
        got["context"] = context
        raise _Stop

    monkeypatch.setattr(main, "get_connection", lambda: _Conn())
    monkeypatch.setattr(main, "_load_computed_fact", lambda conn, m, p: FACT)
    monkeypatch.setattr(main, "active_dataset_id", lambda conn: None)
    monkeypatch.setattr(main, "retrieve", lambda *a, **k: [])
    monkeypatch.setattr(main, "_context_store", lambda conn: None)
    monkeypatch.setattr(main, "shared_embedder", lambda: None)
    monkeypatch.setattr(main, "shared_vector_store", lambda: None)
    monkeypatch.setattr(main, "_enforce_budget", lambda user=None: None)
    monkeypatch.setattr(main, "get_llm_client", lambda: object())
    monkeypatch.setattr(main, "generate_insight", _generate)
    return got


def _call(main):
    req = GenerateInsightRequest(metric="Net Revenue", period="2025-03", context=[FAKE], force=True)
    with pytest.raises(_Stop):
        main.generate_insight_endpoint(req, USER)


def test_supplied_context_is_ignored_with_sign_in_on(monkeypatch, seen):
    import app.main as main
    monkeypatch.setattr(main, "auth_active", lambda: True)
    _call(main)
    assert all(c.id != "ctx_999" for c in seen["context"])


def test_supplied_context_still_works_for_local_development(monkeypatch, seen):
    import app.main as main
    monkeypatch.setattr(main, "auth_active", lambda: False)
    _call(main)
    assert [c.id for c in seen["context"]] == ["ctx_999"]
