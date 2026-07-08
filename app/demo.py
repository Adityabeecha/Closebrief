"""Demo mode (v2.9): seed a sample FP&A dataset so a first-time visitor can
explore the product without uploading anything. Idempotent — keyed on the
dataset name — and never steals the active slot from a real dataset.

Enabled with DEMO_MODE=true; seeding runs once at startup, and unauthenticated
visitors get a read-only "demo" session (see the auth middleware).
"""

from __future__ import annotations

import json
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
    added_docs = []
    if context_store is not None:
        for doc in _DEMO_CONTEXT:
            try:
                added_docs.append(context_store.add(ContextDocIn(**doc)))
            except Exception:  # noqa: BLE001 - demo seeding must never block startup
                pass

    # Pre-generated narratives + a PVM bridge so the demo is full on first view,
    # with source chips, verified badges, an anomaly, and a rendered waterfall —
    # no LLM call and no clicks required.
    try:
        _seed_narratives(conn, ds, added_docs)
        _seed_pvm_bridge(conn, ds)
        conn.commit()
    except Exception:  # noqa: BLE001 - demo polish is best-effort
        pass
    return True


def _latest_period(conn, ds: int) -> str | None:
    row = conn.execute(
        """SELECT MAX(cf.period) AS p FROM computed_facts cf
           JOIN metrics m ON m.id = cf.metric_id WHERE m.dataset_id = ?""",
        (ds,),
    ).fetchone()
    return row["p"] if row and row["p"] else None


def _seed_narratives(conn, ds: int, docs: list) -> None:
    """Deterministic, grounded narratives from each metric's real computed fact
    plus a matching context note — persisted like any generated report."""
    period = _latest_period(conn, ds)
    if not period:
        return
    # Map a metric-tag keyword to the seeded context doc for source chips.
    tag_to_doc = {}
    for d in docs:
        for tag in (getattr(d, "metric_tags", None) or []):
            tag_to_doc[tag.lower()] = d

    facts = conn.execute(
        """SELECT m.id AS metric_id, m.name AS metric, cf.value, cf.mom_pct,
                  cf.budget_var_pct, cf.is_anomaly, m.unit, m.direction_good
           FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
           WHERE m.dataset_id = ? AND cf.period = ?""",
        (ds, period),
    ).fetchall()

    def fmt_val(v, unit):
        v = float(v or 0)
        u = (unit or "USD").lower()
        if u in ("%", "percent"):
            return f"{v:.1f}%"
        if u in ("count", "customers", "logos"):
            return f"{v:,.0f}"
        return f"${v/1e6:.2f}M" if abs(v) >= 1e6 else f"${v/1e3:.0f}K"

    for f in facts:
        mom = f["mom_pct"]
        bud = f["budget_var_pct"]
        doc = None
        for kw, d in tag_to_doc.items():
            if kw in f["metric"].lower():
                doc = d
                break
        move = "rose" if (mom or 0) > 0 else "declined" if (mom or 0) < 0 else "held flat"
        vs_plan = ("ahead of" if (bud or 0) > 0 else "behind") + " plan"
        val = fmt_val(f["value"], f["unit"])
        narr = (
            f"{f['metric']} came in at {val} for {period}, {move} "
            f"{abs(mom):.1f}% month-over-month and {abs(bud or 0):.1f}% {vs_plan}."
            if mom is not None else
            f"{f['metric']} came in at {val} for {period}, {abs(bud or 0):.1f}% {vs_plan}."
        )
        if doc is not None:
            narr += f" This aligns with the note “{doc.title}.”"
        if f["is_anomaly"]:
            narr += " The move is flagged as anomalous versus its recent trend and warrants a closer look."
        sources = (
            [{"id": f"ctx_{doc.id:03d}", "type": doc.type, "title": doc.title}] if doc is not None else []
        )
        conn.execute(
            """INSERT INTO generated_reports
               (metric_id, period, narrative, sources, confidence, faithfulness, prompt_version)
               VALUES (?, ?, ?, ?, ?, 'passed', 'demo')""",
            (f["metric_id"], period, narr, json.dumps(sources),
             "High" if doc is not None else "Medium"),
        )


def _seed_pvm_bridge(conn, ds: int) -> None:
    """Insert a price/volume/mix bridge for a revenue metric so the demo's
    metric-detail view renders a real waterfall."""
    period = _latest_period(conn, ds)
    if not period:
        return
    row = conn.execute(
        """SELECT m.id AS metric_id, cf.budget_var_abs
           FROM metrics m JOIN computed_facts cf ON cf.metric_id = m.id
           WHERE m.dataset_id = ? AND cf.period = ? AND lower(m.name) LIKE '%revenue%'
           LIMIT 1""",
        (ds, period),
    ).fetchone()
    if row is None:
        return
    total = float(row["budget_var_abs"] or 900000.0)
    volume, price, mix = round(total * 0.55, 2), round(total * 0.30, 2), round(total * 0.15, 2)
    conn.execute(
        """INSERT INTO pvm_bridges (metric_id, period, volume, price, mix, total, n_items)
           VALUES (?, ?, ?, ?, ?, ?, 1)
           ON CONFLICT(metric_id, period) DO NOTHING""",
        (row["metric_id"], period, volume, price, mix, total),
    )
