"""Demo mode (v2.9): seed a sample FP&A dataset so a first-time visitor can
explore the product without uploading anything. Idempotent — keyed on the
dataset name — and never steals the active slot from a real dataset.

Enabled with DEMO_MODE=true; seeding runs once at startup, and unauthenticated
visitors get a read-only "demo" session (see the auth middleware).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from app.compute.kpis import compute_and_store
from app.datasets import create_dataset
from app.domains.marketing import MARKETING
from app.generation.prompts import _fixed
from app.ingestion.ingest import ingest_dataframe, parse_csv
from app.kpis.library import suggest_kpi
from app.scheduling import record_digest_run
from app.schemas import ContextDocIn

DEMO_DATASET_NAME = "Demo — Sample FP&A"
_SAMPLE_CSV = Path(__file__).resolve().parent.parent / "data" / "sample_fpa.csv"

DEMO_MARKETING_NAME = "Demo — Marketing Funnel"
_SAMPLE_MARKETING_CSV = Path(__file__).resolve().parent.parent / "data" / "sample_marketing.csv"

# Curated notes that make the RAG loop visible in the demo: narratives cite
# them as source chips, and two of them intentionally conflict.
_DEMO_CONTEXT = [
    {"type": "event_note", "title": "March 2025 enterprise pricing change",
     "body": "On March 1 2025 we raised enterprise plan prices by 15%. Expect Net Revenue "
             "uplift of roughly $600K/month from March onward with some churn risk in SMB.",
     "metric_tags": ["Net Revenue"], "effective_date": "2025-03"},
    {"type": "event_note", "title": "Q1 brand campaign",
     "body": "Brand awareness campaign ran January through March 2025 with a $450K budget, "
             "concentrated in February. Expect elevated Marketing Spend in Q1.",
     "metric_tags": ["Marketing Spend"], "effective_date": "2025-02"},
    {"type": "event_note", "title": "Churn analysis memo",
     "body": "Churned ARR in Q1 2025 tracked at approximately $580K, driven primarily by "
             "two SMB logo losses in February.",
     "metric_tags": ["Churned ARR"], "effective_date": "2025-03"},
]

# Notes that explain the marketing funnel's moves (Feb push, April CTR dip).
_MARKETING_CONTEXT = [
    {"type": "event_note", "title": "February paid brand push",
     "body": "A concentrated paid brand campaign ran in February 2025, lifting Impressions "
             "roughly 55% and pulling Conversions up with them.",
     "metric_tags": ["Impressions", "Conversions"], "effective_date": "2025-02"},
    {"type": "event_note", "title": "April creative fatigue",
     "body": "Ad creative fatigued through April 2025; click-through fell from ~4.3% to ~3.1%, "
             "so Clicks came in behind plan and dragged the rest of the funnel.",
     "metric_tags": ["Clicks"], "effective_date": "2025-04"},
]


def seed_demo(conn, context_store=None) -> bool:
    """Seed the demo datasets (FP&A + Marketing funnel) if absent. Each is
    idempotent by name and lives in the demo universe (is_demo). Returns True if
    any seeding ran."""
    seeded = _seed_fpa(conn, context_store)
    seeded = _seed_marketing(conn, context_store) or seeded
    return seeded


def _select_kpis(conn, ds: int, picker) -> None:
    """Insert kpi_configs for each of the dataset's metrics using `picker(name)`
    -> {name, category, unit, direction_good} | None."""
    metrics = [r["name"] for r in conn.execute(
        "SELECT name FROM metrics WHERE dataset_id = ?", (ds,)
    ).fetchall()]
    for m in metrics:
        s = picker(m)
        if s is None:
            continue
        conn.execute(
            """INSERT INTO kpi_configs (dataset_id, source_metric, display_name, category, unit, direction_good)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(dataset_id, source_metric) DO NOTHING""",
            (ds, m, s.get("name", m), s["category"], s["unit"], s["direction_good"]),
        )


def _seed_context(conn, context_store, docs: list) -> list:
    added = []
    if context_store is None:
        return added
    for doc in docs:
        try:
            d = context_store.add(ContextDocIn(**doc))
            conn.execute(
                "UPDATE context_documents SET is_demo = true WHERE id = ?", (d.id,)
            )
            added.append(d)
        except Exception:  # noqa: BLE001 - demo seeding must never block startup
            pass
    conn.commit()
    return added


def _seed_fpa(conn, context_store=None) -> bool:
    if conn.execute("SELECT id FROM datasets WHERE name = ?", (DEMO_DATASET_NAME,)).fetchone():
        return False
    if not _SAMPLE_CSV.exists():
        return False
    # Its own isolated universe (is_demo), active within the demo scope only.
    # uploaded_by is a UUID column on Postgres, so the seeder passes NULL.
    ds = create_dataset(conn, DEMO_DATASET_NAME, activate=True, is_demo=True,
                        uploaded_by=None, uploaded_by_email="demo@closebrief.app")
    ingest_dataframe(conn, parse_csv(_SAMPLE_CSV.read_bytes()), ds)
    compute_and_store(conn, ds)
    _select_kpis(conn, ds, suggest_kpi)   # curate via the library
    conn.commit()

    added_docs = _seed_context(conn, context_store, _DEMO_CONTEXT)
    # Pre-generated narratives + a PVM bridge so the demo is full on first view.
    try:
        _seed_narratives(conn, ds, added_docs)
        _seed_close_history(conn, ds, added_docs)
        _seed_pvm_bridge(conn, ds)
        conn.commit()
    except Exception:  # noqa: BLE001 - demo polish is best-effort
        pass
    return True


def _seed_marketing(conn, context_store=None) -> bool:
    """The Growth demo (Phase 1): a marketing dataset on the 'marketing' domain so
    the acquisition funnel renders out of the box. Seeded inactive so FP&A stays
    the default view; a visitor switches to it via the workspace switcher."""
    if conn.execute("SELECT id FROM datasets WHERE name = ?", (DEMO_MARKETING_NAME,)).fetchone():
        return False
    if not _SAMPLE_MARKETING_CSV.exists():
        return False
    ds = create_dataset(conn, DEMO_MARKETING_NAME, activate=False, is_demo=True,
                        uploaded_by=None, uploaded_by_email="demo@closebrief.app")
    conn.execute("UPDATE datasets SET domain = 'marketing' WHERE id = ?", (ds,))
    ingest_dataframe(conn, parse_csv(_SAMPLE_MARKETING_CSV.read_bytes()), ds)
    compute_and_store(conn, ds)
    # Curate KPIs from the marketing domain library (funnel stages + CAC/ROAS).
    lib = {k["name"]: k for k in MARKETING.kpi_library}
    _select_kpis(conn, ds, lambda m: lib.get(m))
    conn.commit()

    added_docs = _seed_context(conn, context_store, _MARKETING_CONTEXT)
    try:
        _seed_narratives(conn, ds, added_docs)
        _seed_close_history(conn, ds, added_docs)
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


def format_figure(value: float | None, unit: str = "USD", *, signed: bool = False) -> str:
    """Mirror the Figures panel's fmt/pct, including JavaScript rounding."""
    if value is None or not math.isfinite(value):
        return "–"
    sign = "−" if value < 0 else "+" if signed and value > 0 else ""
    amount = abs(value)
    if unit == "%":
        return sign + _fixed(amount, 1) + "%"
    if unit == "count":
        rounded = math.floor(value + 0.5)
        return ("−" if rounded < 0 else "") + f"{abs(rounded):,}"
    if amount >= 1e6:
        return sign + "$" + _fixed(amount / 1e6, 2) + "M"
    if amount >= 1e3:
        return sign + "$" + _fixed(amount / 1e3, 0) + "K"
    return sign + "$" + _fixed(amount, 2).rstrip("0").rstrip(".")


def _demo_reason(doc, period: str, mom: float | None) -> str | None:
    """Only curated, period-relevant notes may supply a reason; no new figures."""
    if doc is None or (doc.effective_date and doc.effective_date[:7] > period):
        return None
    title = doc.title
    if title == "March 2025 enterprise pricing change" and mom is not None and mom > 0:
        return f'The note "{title}" records the enterprise price increase behind the uplift.'
    if title == "Q1 brand campaign" and period <= "2025-03":
        return f'The note "{title}" records the campaign behind elevated marketing spend.'
    if title == "Churn analysis memo" and period <= "2025-03":
        return f'The note "{title}" attributes churn to SMB logo losses.'
    if title == "February paid brand push" and period == "2025-02" and mom is not None and mom > 0:
        return f'The note "{title}" records the campaign that lifted impressions and conversions.'
    if title == "April creative fatigue" and period == "2025-04" and mom is not None and mom < 0:
        return f'The note "{title}" attributes the decline in clicks to fatigued ad creative.'
    return None


def _seed_narratives(conn, ds: int, docs: list, period: str | None = None) -> None:
    """Deterministic, grounded narratives from each metric's real computed fact
    plus a matching context note — persisted like any generated report."""
    period = period or _latest_period(conn, ds)
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
           WHERE m.dataset_id = ? AND cf.period = ? AND cf.value IS NOT NULL
           ORDER BY m.name""",
        (ds, period),
    ).fetchall()

    for f in facts:
        if conn.execute("SELECT 1 FROM generated_reports WHERE metric_id=? AND period=?", (f["metric_id"], period)).fetchone():
            continue
        bud, mom = f["budget_var_pct"], f["mom_pct"]
        val = format_figure(f["value"], f["unit"])
        if bud is not None:
            narr = f"{f['metric']} reached {val}, {format_figure(bud, '%', signed=True)} vs plan."
        elif mom is not None:
            narr = f"{f['metric']} reached {val}, {format_figure(mom, '%', signed=True)} vs last month. No budget comparison is available."
        else:
            narr = f"{f['metric']} reached {val}. No budget or last-month comparison is available."
        doc = tag_to_doc.get(f["metric"].lower())
        reason = _demo_reason(doc, period, mom)
        if reason:
            narr += " " + reason
        else:
            doc = None
            narr += " No reason recorded yet."
        if f["is_anomaly"]:
            narr += " Watch next month for a repeat of this unusual movement."
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


def _seed_close_history(conn, ds: int, docs: list) -> None:
    """Two completed closes lead into a partly approved latest close, without AI."""
    periods = [r["period"] for r in conn.execute(
        """SELECT DISTINCT cf.period FROM computed_facts cf JOIN metrics m ON m.id=cf.metric_id
           WHERE m.dataset_id=? ORDER BY cf.period DESC LIMIT 3""", (ds,)).fetchall()]
    if not periods:
        return
    for period in reversed(periods[1:]):
        _seed_narratives(conn, ds, docs, period)
        conn.execute("""UPDATE generated_reports SET review_status='approved'
                        WHERE period=? AND metric_id IN (SELECT id FROM metrics WHERE dataset_id=?)""", (period, ds))
        if conn.execute("SELECT 1 FROM digest_runs WHERE dataset_id=? AND period=?", (ds, period)).fetchone():
            continue
        movers = conn.execute("""SELECT m.name, cf.value, cf.budget_var_abs, cf.budget_var_pct, gr.id, gr.narrative
            FROM computed_facts cf JOIN metrics m ON m.id=cf.metric_id
            JOIN generated_reports gr ON gr.id=(SELECT MAX(g.id) FROM generated_reports g WHERE g.metric_id=m.id AND g.period=cf.period)
            WHERE m.dataset_id=? AND cf.period=? AND cf.value IS NOT NULL
            ORDER BY ABS(COALESCE(cf.budget_var_abs,0)) DESC, m.name LIMIT 3""", (ds, period)).fetchall()
        items = [{"metric": r["name"], "headline": r["narrative"].split('. ', 1)[0] + '.',
                  "detail": r["narrative"], "budget_var_abs": r["budget_var_abs"],
                  "budget_var_pct": r["budget_var_pct"], "faithfulness": "passed", "report_id": r["id"]} for r in movers]
        run_id = record_digest_run(conn, ds, period, 3, items, None, 'scheduled')
        y, m = int(period[:4]), int(period[5:7])
        sent = f"{y + (m == 12)}-{m % 12 + 1:02d}-03 09:00:00"   # the digest goes out early the next month
        conn.execute("UPDATE digest_runs SET created_at=? WHERE id=?", (sent, run_id))
    # Deterministic minority approved; the rest still need the analyst's decision.
    conn.execute("""UPDATE generated_reports SET review_status='approved' WHERE id IN (
        SELECT gr.id FROM generated_reports gr JOIN metrics m ON m.id=gr.metric_id
        WHERE m.dataset_id=? AND gr.period=? ORDER BY m.name LIMIT 2)""", (ds, periods[0]))
    conn.commit()
