from pathlib import Path

import pandas as pd
import pytest
from dbharness import use_test_db

from app.ingestion.mapping import MappingSpec, normalize, sanitize_label
from app.ingestion.profiler import profile_columns, suggest_mapping
from app.ingestion.upload import detect_header_row

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv"

TITLE = "Ridgeline Roasters - Loyalty Program & Local Marketing Spend"
SUBTITLE = "Source: loyalty app admin console + AP ledger export."
HEADER = ["Metric", "Jul-24", "Aug-24", "Sep-24", "Oct-24"]
DATA = ["New Loyalty Signups", "359", "320", "310", "362"]


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


@pytest.mark.parametrize("pad", ["", "   ", None], ids=["empty-string", "whitespace", "true-blank"])
def test_title_rows_padded_any_way_do_not_become_the_header(pad):
    frame = pd.DataFrame([
        [TITLE, pad, pad, pad, pad],
        [SUBTITLE, pad, pad, pad, pad],
        HEADER,
        DATA,
    ])
    assert detect_header_row(frame) == 2


def test_a_real_row_zero_header_is_still_detected():
    frame = pd.DataFrame([HEADER, DATA])
    assert detect_header_row(frame) == 0


def test_loyalty_workbook_headers_on_the_metric_row(client):
    with open(FIXTURES / "ridgeline_loyalty.xlsx", "rb") as f:
        up = client.post("/ingest/upload",
                         files={"file": ("ridgeline_loyalty.xlsx", f, XLSX_MIME)}).json()
    schema = client.get(f"/ingest/{up['upload_id']}/schema").json()

    names = [c["column_name"] for c in schema["columns"]]
    assert "Metric" in names
    assert not any(n.startswith("Unnamed") for n in names), names
    assert not any("Ridgeline Roasters" in n for n in names), names

    assert len(schema["suggested_mapping"]["wide_period_cols"]) == 24


def test_loyalty_workbook_normalizes_its_four_metrics(client):
    with open(FIXTURES / "ridgeline_loyalty.xlsx", "rb") as f:
        up = client.post("/ingest/upload",
                         files={"file": ("ridgeline_loyalty.xlsx", f, XLSX_MIME)}).json()
    schema = client.get(f"/ingest/{up['upload_id']}/schema").json()
    result = client.post(f"/ingest/{up['upload_id']}/mapping",
                         json=schema["suggested_mapping"]).json()

    assert set(result["metrics"]) == {
        "New Loyalty Signups", "Loyalty Redemption Cost",
        "Paid Social Spend", "Local Ads Retainer",
    }
    assert len(result["periods"]) == 24


@pytest.mark.parametrize("value", [float("nan"), None, "nan", "NaN", "None", "", "   "])
def test_a_missing_label_is_empty_not_the_word_nan(value):
    assert sanitize_label(value) == ""


def test_blank_label_rows_never_become_metrics():
    df = pd.read_csv(FIXTURES / "store_channel_pl_monthly.csv", dtype=str)
    suggestion = suggest_mapping(profile_columns(df))
    canonical = normalize(df, MappingSpec(**suggestion["mapping"]))

    names = set(canonical.attrs.get("all_metrics", [])) | set(canonical["metric"].unique())
    assert names == {
        "Retail Cafe net_revenue_usd", "Retail Cafe cogs_usd",
        "Wholesale net_revenue_usd", "Wholesale cogs_usd",
        "Catering net_revenue_usd", "Catering cogs_usd",
    }


def test_store_channel_file_exposes_no_nan_kpi(client):
    with open(FIXTURES / "store_channel_pl_monthly.csv", "rb") as f:
        up = client.post("/ingest/upload",
                         files={"file": ("store_channel_pl_monthly.csv", f, CSV_MIME)}).json()
    schema = client.get(f"/ingest/{up['upload_id']}/schema").json()
    result = client.post(f"/ingest/{up['upload_id']}/mapping",
                         json=schema["suggested_mapping"]).json()

    assert len(result["metrics"]) == 6
    for name in result["metrics"]:
        assert name.strip().lower() not in ("nan", "none", "")

    available = client.get("/kpis").json()
    offered = {k["source_metric"] for k in available["selected"]} | set(available["available"])
    assert "nan" not in offered
    assert "None" not in offered
