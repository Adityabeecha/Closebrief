"""v3.0: /ask does one stricter retry when the first answer isn't faithful, so a
Q&A answer never ships an unverifiable figure (success criterion: faithfulness)."""

import pytest
from dbharness import use_test_db


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5
    cost_usd = 0.001


class _Result:
    def __init__(self, narrative):
        self.narrative = narrative
        self.sources_used = []


class _FakeLLM:
    """Returns answers in sequence; records how many times it was called."""
    def __init__(self, answers):
        self.answers = answers
        self.calls = 0

    def generate_narrative(self, system, prompt):
        a = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return _Result(a), _Usage()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main
    from app.config import settings

    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "vector_backend", "faiss")
    monkeypatch.setattr(settings, "embedding_provider", "offline")
    monkeypatch.setattr(settings, "supabase_url", "")

    from app.deps import shared_cache, shared_embedder, shared_vector_store
    shared_cache.cache_clear()
    shared_embedder.cache_clear()
    shared_vector_store.cache_clear()

    main.init_db()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def _ingest(client):
    csv = "period,metric,value,budget\n2025-02,Net Revenue,4180000,4100000\n2025-03,Net Revenue,5330000,4730000\n"
    uid = client.post("/ingest/upload", files={"file": ("f.csv", csv, "text/csv")}).json()["upload_id"]
    client.post(f"/ingest/{uid}/mapping", json={
        "layout": "long", "period_col": "period", "metric_col": "metric",
        "value_col": "value", "budget_col": "budget"})


def test_ask_retries_once_when_unfaithful(client, monkeypatch):
    import app.main as main
    _ingest(client)
    # First answer invents a figure; the stricter retry answers without numbers.
    fake = _FakeLLM([
        "Revenue was $999,999,999 this month, up 4321%.",
        "Revenue increased this period, consistent with the pricing change.",
    ])
    monkeypatch.setattr(main, "get_llm_client", lambda: fake)

    r = client.post("/ask", json={"metric": "Net Revenue", "period": "2025-03",
                                   "question": "Why did revenue rise?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert fake.calls == 2                 # it retried
    assert body["grounded"] is True        # the retry is clean
    assert "999,999,999" not in body["answer"]


def test_ask_no_retry_when_first_answer_clean(client, monkeypatch):
    import app.main as main
    _ingest(client)
    fake = _FakeLLM(["Revenue increased this period, per the pricing note."])
    monkeypatch.setattr(main, "get_llm_client", lambda: fake)

    r = client.post("/ask", json={"metric": "Net Revenue", "period": "2025-03",
                                   "question": "Why?"})
    assert r.status_code == 200
    assert fake.calls == 1                  # no retry needed
    assert r.json()["grounded"] is True
