from pathlib import Path

import pytest
from dbharness import use_test_db

REAL = Path(__file__).parent / "fixtures" / "ingestion"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


def _rebuild_like_ui(schema):
    roles = {c["column_name"]: c["guessed_role"] for c in schema["columns"]}
    by = lambda r: [c for c, v in roles.items() if v == r]  # noqa: E731
    suggested = schema["suggested_mapping"]
    period_literal = suggested.get("period_literal")
    measures = by("measure")
    base = {
        "layout": "long",
        "period_col": None if period_literal else (by("period")[0] if by("period") else None),
        "period_literal": period_literal,
        "metric_col": by("metric_label")[0] if by("metric_label") else None,
        "budget_col": by("budget")[0] if by("budget") else None,
        "id_col": by("id")[0] if by("id") else None,
        "dimension_cols": by("dimension"),
    }
    if len(measures) > 1:
        return {**base, "value_cols": measures}
    return {**base, "value_col": measures[0] if measures else None}


def test_ui_rebuilt_mapping_keeps_sheet_period(client):
    with open(REAL / "finance_ar_aging.xlsx", "rb") as f:
        up = client.post(
            "/ingest/upload", files={"file": ("finance_ar_aging.xlsx", f, XLSX_MIME)}
        ).json()

    schema = client.get(
        f"/ingest/{up['upload_id']}/schema", params={"sheet": "AR Dec-25"}
    ).json()

    mapping = _rebuild_like_ui(schema)
    assert mapping["period_literal"] == "2025-12"
    assert mapping["period_col"] is None

    r = client.post(f"/ingest/{up['upload_id']}/mapping", json=mapping)
    assert r.status_code == 200, r.text
    assert r.json()["periods"] == ["2025-12"]
