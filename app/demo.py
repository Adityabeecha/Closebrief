"""Demo mode (v2.9): seed a sample FP&A dataset so a first-time visitor can
explore the product without uploading anything. Idempotent — keyed on the
dataset name — and never steals the active slot from a real dataset.

Enabled with DEMO_MODE=true; seeding runs once at startup, and unauthenticated
visitors get a read-only "demo" session (see the auth middleware).
"""

from __future__ import annotations

from pathlib import Path

from app.compute.kpis import compute_and_store
from app.datasets import create_dataset
from app.ingestion.ingest import ingest_dataframe, parse_csv
from app.kpis.library import suggest_kpi
from app.schemas import ContextDocIn

DEMO_DATASET_NAME = "Demo — Sample FP&A"
_SAMPLE_CSV = Path(__file__).resolve().parent.parent / "data" / "sample_fpa.csv"

# Curated notes that make the RAG loop visible in the demo: narratives cite
# them as source chips, and two of them intentionally conflict.
_DEMO_CONTEXT = [
    {"type": "event_note", "title": "March 2025 enterprise pricing change",
     "body": "On March 1 2025 we raised enterprise plan prices by 15%. Expect Net Revenue "
             "uplift of roughly $600K/month from March onward with some churn risk in SMB.",
     "metric_tags": ["Net Revenue"], "effective_date": "2025-03"},
    {"type": "campaign", "title": "Q1 brand campaign",
     "body": "Brand awareness campaign ran January through March 2025 with a $450K budget, "
             "concentrated in February. Expect elevated Marketing Spend in Q1.",
     "metric_tags": ["Marketing Spend"], "effective_date": "2025-02"},
    {"type": "event_note", "title": "Churn analysis memo",
     "body": "Churned ARR in Q1 2025 tracked at approximately $580K, driven primarily by "
             "two SMB logo losses in February.",
     "metric_tags": ["Churned ARR"], "effective_date": "2025-03"},
]


def seed_demo(conn, context_store=None) -> bool:
    """Create + populate the demo dataset if it doesn't exist. Returns True if
    seeding ran. Never changes the active dataset when one is already active."""
    row = conn.execute(
        "SELECT id FROM datasets WHERE name = ?", (DEMO_DATASET_NAME,)
    ).fetchone()
    if row is not None:
        return False
    if not _SAMPLE_CSV.exists():
        return False

    # Its own isolated universe (is_demo) — active within the demo scope only,
    # never touching the operator's real datasets. uploaded_by is a UUID column
    # on Postgres, so the seeder passes NULL.
    ds = create_dataset(conn, DEMO_DATASET_NAME, activate=True, is_demo=True,
                        uploaded_by=None, uploaded_by_email="demo@closebrief.app")

    df = parse_csv(_SAMPLE_CSV.read_bytes())
    ingest_dataframe(conn, df, ds)
    compute_and_store(conn, ds)

    # Pre-select KPIs via the library so the dashboard is curated, not raw.
    metrics = [r["name"] for r in conn.execute(
        "SELECT name FROM metrics WHERE dataset_id = ?", (ds,)
    ).fetchall()]
    for m in metrics:
        s = suggest_kpi(m)
        if s is None:
            continue
        conn.execute(
            """INSERT INTO kpi_configs (dataset_id, source_metric, display_name, category, unit, direction_good)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(dataset_id, source_metric) DO NOTHING""",
            (ds, m, s["name"], s["category"], s["unit"], s["direction_good"]),
        )
    conn.commit()

    # Context notes (best-effort — embedding may need a network/API key).
    if context_store is not None:
        for doc in _DEMO_CONTEXT:
            try:
                context_store.add(ContextDocIn(**doc))
            except Exception:  # noqa: BLE001 - demo seeding must never block startup
                pass
    return True
