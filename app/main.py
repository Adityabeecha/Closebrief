import json
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.auth import CurrentUser, authenticate, get_current_user, invalidate_role_cache, require_role
from app.cache import make_insight_key
from app.compute.correlations import (
    compare_periods,
    compute_correlations,
    correlations_for_metric,
    detect_consecutive_trends,
)
from app.compute.kpis import compute_and_store
from app.compute.pvm import bridge_for_metric_row
from app.config import auth_active, resolved_vector_backend, settings
from app.context.conflicts import find_conflicts
from app.context.store import ContextStore
from app.datasets import (
    active_dataset_id,
    create_dataset,
    delete_dataset,
    list_datasets,
    set_active,
    set_demo_scope,
)
from app.db import get_connection, init_db
from app.demo import seed_demo
from app.deps import shared_cache, shared_embedder, shared_vector_store
from app.digest.digest import DigestOutput, generate_digest
from app.domains import get_domain, list_domains, registry
from app.generation.generate import GenerationFailedFactsOnly, generate_insight
from app.generation.guard import check_faithfulness
from app.generation.llm_client import LLMGenerationError, get_llm_client
from app.generation.prompts import PROMPT_VERSION, QA_SYSTEM_PROMPT, build_qa_prompt
from app.ingestion.ingest import IngestValidationError, ingest_dataframe, parse_csv
from app.ingestion.mapping import MappingError, MappingSpec, normalize, store_normalized
from app.ingestion.profiler import profile_columns
from app.ingestion.templates import get_template as load_template
from app.ingestion.templates import save_template as save_mapping_template
from app.ingestion.upload import UploadError, create_upload, load_upload_frame
from app.kpis.library import KPI_LIBRARY, suggest_kpi
from app.notifications.channels import NotificationError, make_channel
from app.notifications.scheduler import deliver as notif_deliver
from app.notifications.scheduler import list_configs as notif_list_configs
from app.retrieval.retrieve import retrieve
from app.schemas import (
    ComputedFact,
    ContextDoc,
    ContextDocIn,
    ContextSnippet,
    CorrelationPair,
    Deltas,
    FeedbackIn,
    GenerateInsightRequest,
    IngestSummary,
    InsightOutput,
    KPIConfigPayload,
    TrendStreak,
)

# Error + performance monitoring (v2.0). No-op unless SENTRY_DSN is set, so it
# stays silent in local dev and tests.
if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,   # 10% of requests for performance traces
        profiles_sample_rate=0.1,
    )

app = FastAPI(
    title="Closebrief",
    description="RAG-based AI agent for automated FP&A variance commentary. "
    "The LLM never computes numbers — see app/compute/kpis.py for the deterministic layer.",
    version="0.1.0",
)

# CORS: the frontend authenticates against Supabase directly (cross-origin) and
# calls this API with a Bearer token (no cookies), so allow-any-origin is safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Role-guard dependencies (v1.2). read = any authenticated role; write excludes
# executives (read-only); admin only for user management.
require_read = require_role("viewer", "analyst", "executive", "admin")
require_write = require_role("analyst", "admin")   # viewer excluded -> demo writes 403
require_admin = require_role("admin")

# Paths reachable without a token: the app shell, health, the public auth config
# the frontend needs to boot the login form, and the API docs.
_OPEN_PATHS = {"/", "/health", "/auth/config", "/openapi.json", "/docs", "/redoc"}


@app.on_event("startup")
def _startup() -> None:
    init_db()
    if settings.demo_mode:
        # Seed the sample dataset once so first-time visitors see a live board.
        try:
            conn = get_connection()
            try:
                seed_demo(conn, _context_store(conn))
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - demo seeding must never block startup
            pass


# ---- request telemetry: latency ring buffer + naive per-IP rate limit ----

_LATENCIES: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
_RATE_BUCKETS: dict[str, deque] = defaultdict(deque)
_RATE_LIMIT_PER_MINUTE = 240  # generous: the UI's generate-all bursts stay well under


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    """Authentication gate (401). Attaches CurrentUser to request.state so
    downstream role guards (403) and attribution can read it. Bypassed for the
    open paths and, entirely, when auth is not active (local dev)."""
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or path in _OPEN_PATHS
        or path.startswith("/docs")
        or path.startswith("/vendor/")   # static frontend libs, needed pre-login
    ):
        return await call_next(request)
    try:
        user = authenticate(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    request.state.user = user
    # Scope every dataset query to the caller's universe: demo (viewer) sessions
    # see only the demo dataset; everyone else sees only real datasets.
    set_demo_scope(user.role == "viewer")
    return await call_next(request)


@app.middleware("http")
async def _telemetry_middleware(request, call_next):
    client_ip = request.client.host if request.client else "?"
    now = time.time()
    bucket = _RATE_BUCKETS[client_ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_PER_MINUTE:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    bucket.append(now)

    t0 = time.perf_counter()
    response = await call_next(request)
    _LATENCIES[request.url.path].append((time.perf_counter() - t0) * 1000)
    return response


@app.get("/auth/config")
def auth_config() -> dict:
    """Public: the frontend uses this to decide whether to show the login gate
    and to initialize the Supabase JS client. Never returns secrets."""
    return {
        "auth_enabled": auth_active(),
        "supabase_url": settings.supabase_url,
        "supabase_anon_key": settings.supabase_anon_key,
        # Public by design (a DSN is not a secret); enables frontend error tracking.
        "sentry_dsn": settings.sentry_dsn,
        "environment": settings.environment,
        "demo_enabled": settings.demo_mode,
    }


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))
    return round(ordered[idx], 1)


def _notify_async(event: str, period: str | None = None, items: list[dict] | None = None) -> None:
    """Fan a notification event out to configured channels on a daemon thread —
    delivery (SMTP/HTTP) must never add latency or failures to the request."""
    import threading

    def _run():
        try:
            conn = get_connection()
            try:
                notif_deliver(conn, event, period=period, items=items)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - best-effort by design
            pass

    threading.Thread(target=_run, daemon=True).start()


def _notify_anomalies(conn, dataset_id: int) -> None:
    """After a compute pass: alert channels about anomalies in the dataset's
    latest period (if any)."""
    rows = conn.execute(
        """
        SELECT m.name AS metric, cf.period, cf.value, cf.mom_pct
        FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
        WHERE m.dataset_id = ? AND cf.is_anomaly AND cf.period = (
            SELECT MAX(cf2.period) FROM computed_facts cf2
            JOIN metrics m2 ON m2.id = cf2.metric_id WHERE m2.dataset_id = ?
        )
        """,
        (dataset_id, dataset_id),
    ).fetchall()
    if not rows:
        return
    items = [{
        "metric": r["metric"], "period": r["period"],
        "value": f"{r['value']:,.0f}",
        "delta": f"{r['mom_pct']:+.1f}% MoM" if r["mom_pct"] is not None else "",
    } for r in rows]
    _notify_async("anomaly_detected", items=items)


def _log_llm_call(endpoint: str, model, prompt_tokens, completion_tokens, cost_usd, latency_ms, user_id=None):
    try:
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO llm_calls (endpoint, model, prompt_tokens,
                       completion_tokens, cost_usd, latency_ms, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (endpoint, model, prompt_tokens, completion_tokens, cost_usd, latency_ms, user_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # cost logging must never fail a request


_UI_INDEX = Path(__file__).resolve().parent.parent / "ui" / "web" / "index.html"


@app.get("/", include_in_schema=False)
def ui_root() -> FileResponse:
    # no-cache: browsers must revalidate so UI fixes are picked up immediately
    # (stale cached copies of this shell caused hard-to-debug auth errors).
    return FileResponse(_UI_INDEX, headers={"Cache-Control": "no-cache"})


# Vendored frontend libraries served from our own origin (no CDN dependency, so
# the app works offline / behind ad-blockers / strict networks).
_UI_VENDOR = _UI_INDEX.parent / "vendor"


@app.get("/vendor/{filename:path}", include_in_schema=False)
def ui_vendor(filename: str) -> FileResponse:
    path = (_UI_VENDOR / filename).resolve()
    if _UI_VENDOR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    # Windows mimetypes lacks woff2; wrong MIME makes browsers reject the font.
    media_type = "font/woff2" if path.suffix == ".woff2" else None
    return FileResponse(path, media_type=media_type)


@app.get("/health")
def health() -> dict:
    """v1.1 Phase 4: report DB, vector store, and cache status, not just 200."""
    checks: dict[str, dict] = {}

    db_backend = "postgres" if settings.database_url else "sqlite"
    try:
        conn = get_connection()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        checks["db"] = {"backend": db_backend, "ok": True}
    except Exception as exc:
        checks["db"] = {"backend": db_backend, "ok": False, "error": str(exc)[:120]}

    try:
        vs = shared_vector_store()
        checks["vector_store"] = {
            "backend": getattr(vs, "backend", "faiss"),
            "ok": True,
            "vectors": vs.size,
        }
    except Exception as exc:
        checks["vector_store"] = {"backend": resolved_vector_backend(), "ok": False, "error": str(exc)[:120]}

    cache = shared_cache()
    checks["cache"] = {"backend": cache.backend, "ok": cache.ping()}

    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "environment": settings.environment,
        **checks,
    }


@app.get("/costs")
def costs(_: CurrentUser = Depends(require_read)) -> dict:
    """v1.2: spend by day and endpoint (from llm_calls), request latency
    p50/p95 (in-process ring buffer), and cache hit rate."""
    conn = get_connection()
    try:
        if getattr(conn, "backend", "sqlite") == "postgres":
            day_expr = "to_char(created_at, 'YYYY-MM-DD')"
        else:
            day_expr = "date(created_at)"
        by_day = [
            {**dict(r), "cost_usd": round(float(r["cost_usd"] or 0), 6)}
            for r in conn.execute(
                f"""SELECT {day_expr} AS day, endpoint,
                        COUNT(*) AS calls,
                        SUM(COALESCE(prompt_tokens,0)) AS prompt_tokens,
                        SUM(COALESCE(completion_tokens,0)) AS completion_tokens,
                        SUM(COALESCE(cost_usd,0)) AS cost_usd
                    FROM llm_calls GROUP BY {day_expr}, endpoint
                    ORDER BY day DESC, endpoint"""
            ).fetchall()
        ]
        total = conn.execute(
            "SELECT COUNT(*) AS calls, SUM(COALESCE(cost_usd,0)) AS cost_usd FROM llm_calls"
        ).fetchone()
    finally:
        conn.close()

    latency = {
        path: {"p50_ms": _percentile(list(v), 50), "p95_ms": _percentile(list(v), 95), "n": len(v)}
        for path, v in _LATENCIES.items()
        if v and path in ("/generate-insight", "/digest", "/facts")
    }
    return {
        "total_llm_calls": total["calls"] or 0,
        "total_cost_usd": round(total["cost_usd"] or 0.0, 6),
        "by_day": by_day,
        "latency": latency,
        "cache": shared_cache().stats(),
    }


@app.get("/correlations")
def correlations_endpoint(
    period: str | None = None, metric: str | None = None,
    _: CurrentUser = Depends(require_read),
) -> list[dict]:
    """Metric pairs that move together over time (|Pearson r| >= 0.7) in the
    active dataset. If `metric` is given, only pairs involving it (phrased with
    that metric first)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return []
        if metric:
            return correlations_for_metric(conn, ds, metric, period)
        return compute_correlations(conn, ds, period)
    finally:
        conn.close()


@app.get("/compare")
def compare_endpoint(
    metric: str, period_a: str, period_b: str,
    _: CurrentUser = Depends(require_read),
) -> dict:
    """Side-by-side comparison of one metric across two periods, including the
    delta-of-deltas (whether the MoM movement accelerated or decelerated)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        mrow = conn.execute(
            "SELECT id FROM metrics WHERE dataset_id = ? AND name = ?", (ds, metric)
        ).fetchone() if ds is not None else None
        if mrow is None:
            raise HTTPException(status_code=404, detail=f"Unknown metric: {metric}")
        result = compare_periods(conn, mrow["id"], period_a, period_b)
        if result is None:
            raise HTTPException(status_code=404, detail="No computed facts for both periods")
        return result
    finally:
        conn.close()


@app.get("/domains")
def list_domains_endpoint(_: CurrentUser = Depends(require_read)) -> list[dict]:
    """Available analytics domains (v2.1 foundation)."""
    return [d.public() for d in list_domains()]


@app.get("/domain")
def get_active_domain(_: CurrentUser = Depends(require_read)) -> dict:
    """The active dataset's domain (defaults to fpa)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        slug = "fpa"
        if ds is not None:
            row = conn.execute("SELECT domain FROM datasets WHERE id = ?", (ds,)).fetchone()
            if row and row["domain"]:
                slug = row["domain"]
        return get_domain(slug).public()
    finally:
        conn.close()


@app.put("/domain")
def set_active_domain(payload: dict, _: CurrentUser = Depends(require_write)) -> dict:
    slug = payload.get("domain")
    if not registry.has(slug or ""):
        raise HTTPException(status_code=422, detail=f"Unknown domain: {slug}")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=400, detail="No active dataset")
        conn.execute("UPDATE datasets SET domain = ? WHERE id = ?", (slug, ds))
        conn.commit()
        return get_domain(slug).public()
    finally:
        conn.close()


@app.get("/notifications/configs")
def list_notification_configs(_: CurrentUser = Depends(require_read)) -> list[dict]:
    conn = get_connection()
    try:
        return notif_list_configs(conn)
    finally:
        conn.close()


@app.post("/notifications/configs", status_code=201)
def create_notification_config(payload: dict, _: CurrentUser = Depends(require_write)) -> dict:
    channel = payload.get("channel")
    if channel not in ("email", "slack", "webhook"):
        raise HTTPException(status_code=422, detail="channel must be email|slack|webhook")
    config = payload.get("config") or {}
    # Use a Python bool: psycopg maps it to Postgres BOOLEAN, sqlite3 to 0/1.
    enabled = bool(payload.get("enabled", True))
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO notification_configs (channel, config, enabled) VALUES (?, ?, ?)",
            (channel, json.dumps(config), enabled),
        )
        conn.commit()
        new_id = cur.lastrowid if hasattr(cur, "lastrowid") and cur.lastrowid else conn.execute(
            "SELECT MAX(id) AS id FROM notification_configs"
        ).fetchone()["id"]
        return {"id": new_id, "channel": channel, "config": config, "enabled": bool(enabled)}
    finally:
        conn.close()


@app.put("/notifications/{config_id}")
def update_notification_config(config_id: int, payload: dict, _: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM notification_configs WHERE id = ?", (config_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="config not found")
        config = payload.get("config")
        enabled = payload.get("enabled")
        if config is not None:
            conn.execute(
                "UPDATE notification_configs SET config = ? WHERE id = ?",
                (json.dumps(config), config_id),
            )
        if enabled is not None:
            conn.execute(
                "UPDATE notification_configs SET enabled = ? WHERE id = ?",
                (bool(enabled), config_id),
            )
        conn.commit()
        return {"id": config_id, "updated": True}
    finally:
        conn.close()


@app.delete("/notifications/{config_id}", status_code=204)
def delete_notification_config(config_id: int, _: CurrentUser = Depends(require_write)) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM notification_configs WHERE id = ?", (config_id,))
        conn.commit()
    finally:
        conn.close()


@app.post("/notifications/test/{config_id}")
def test_notification_config(config_id: int, _: CurrentUser = Depends(require_write)) -> dict:
    """Send a sample anomaly alert through one config to verify delivery."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT channel, config FROM notification_configs WHERE id = ?", (config_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="config not found")
        sample = [{
            "metric": "Net Revenue", "period": "2025-03",
            "value": "$5.33M", "delta": "+27.6% vs plan",
            "narrative": "This is a Closebrief test notification.",
        }]
        try:
            make_channel(row["channel"], json.loads(row["config"] or "{}")).send_anomaly_alert(sample)
        except NotificationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001 - surface delivery failures to the caller
            raise HTTPException(status_code=502, detail=f"Delivery failed: {e}") from e
        return {"ok": True}
    finally:
        conn.close()


@app.get("/context/conflicts")
def context_conflicts(_: CurrentUser = Depends(require_read)) -> list[dict]:
    """Pairs of context documents citing different figures about the same
    thing (mockup's conflict banner). Deterministic heuristic, no LLM."""
    conn = get_connection()
    try:
        docs = _context_store(conn).list()
    finally:
        conn.close()
    return find_conflicts(docs)


@app.get("/datasets")
def get_datasets(_: CurrentUser = Depends(require_read)) -> dict:
    """All datasets + which is active, for the dashboard's dataset switcher."""
    conn = get_connection()
    try:
        return {"datasets": list_datasets(conn), "active_id": active_dataset_id(conn)}
    finally:
        conn.close()


@app.post("/datasets/{dataset_id}/activate")
def activate_dataset(dataset_id: int, _: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
        set_active(conn, dataset_id)
    finally:
        conn.close()
    shared_cache().bump_namespace()
    return {"active_id": dataset_id}


@app.delete("/datasets/{dataset_id}", status_code=204)
def remove_dataset(dataset_id: int, _: CurrentUser = Depends(require_write)) -> None:
    conn = get_connection()
    try:
        ok = delete_dataset(conn, dataset_id)
    finally:
        conn.close()
    if not ok:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
    shared_cache().bump_namespace()


@app.get("/periods")
def list_periods(_: CurrentUser = Depends(require_read)) -> list[str]:
    """Periods that have computed facts in the ACTIVE dataset (so the dashboard
    default lands on a period the active upload actually covers)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return []
        rows = conn.execute(
            """
            SELECT DISTINCT cf.period FROM computed_facts cf
            JOIN metrics m ON m.id = cf.metric_id
            WHERE m.dataset_id = ?
            ORDER BY cf.period
            """,
            (ds,),
        ).fetchall()
        return [r["period"] for r in rows]
    finally:
        conn.close()


def _chart_data(conn, metric_id: int, metric_name: str, period: str) -> dict:
    """chart_data block (Addendum v1.1 Section 4): 12-month trend, budget vs
    actual, and the P/V/M variance bridge (null when no qty/price detail)."""
    trend_rows = conn.execute(
        """
        SELECT period, value, budget FROM metric_values
        WHERE metric_id = ? AND period <= ?
        ORDER BY period DESC LIMIT 12
        """,
        (metric_id, period),
    ).fetchall()
    trend = [
        {"period": r["period"], "value": r["value"], "budget": r["budget"]}
        for r in reversed(trend_rows)
    ]

    current = conn.execute(
        "SELECT value, budget FROM metric_values WHERE metric_id = ? AND period = ?",
        (metric_id, period),
    ).fetchone()
    budget_vs_actual = (
        {"actual": current["value"], "budget": current["budget"]} if current else None
    )

    vb = _load_bridge(conn, metric_name, period)
    bridge = None
    if vb is not None:
        bridge = [
            {"component": "Volume", "impact": vb["volume"]},
            {"component": "Price", "impact": vb["price"]},
            {"component": "Mix", "impact": vb["mix"]},
        ]

    return {"trend": trend, "budget_vs_actual": budget_vs_actual, "variance_bridge": bridge}


@app.get("/facts")
def list_facts(period: str, include_charts: bool = True, _: CurrentUser = Depends(require_read)) -> list[dict]:
    """Dashboard cards for the ACTIVE dataset. Driven by the user's KPI
    selection (kpi_configs): shows exactly the selected KPIs, each LEFT JOINed
    to its computed fact for the period — so a selected KPI with no data for
    this period still renders an empty card (never silently hidden). If no KPIs
    were selected yet, falls back to every metric in the active dataset."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return []

        has_kpis = conn.execute(
            "SELECT 1 FROM kpi_configs WHERE dataset_id = ? LIMIT 1", (ds,)
        ).fetchone() is not None

        if has_kpis:
            # Driver = selected KPIs; category/unit/direction come from the
            # KPI config, metric row is matched by name within the dataset.
            rows = conn.execute(
                """
                SELECT m.id AS metric_id, k.source_metric AS metric,
                       k.category, k.unit, k.direction_good,
                       cf.value, cf.prior_value, cf.mom_pct, cf.yoy_pct,
                       cf.budget_var_abs, cf.budget_var_pct, cf.trend, cf.is_anomaly
                FROM kpi_configs k
                LEFT JOIN metrics m ON m.dataset_id = k.dataset_id AND m.name = k.source_metric
                LEFT JOIN computed_facts cf ON cf.metric_id = m.id AND cf.period = ?
                WHERE k.dataset_id = ?
                ORDER BY ABS(COALESCE(cf.budget_var_abs, 0)) DESC, k.source_metric
                """,
                (period, ds),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.id AS metric_id, m.name AS metric, m.category, m.unit,
                       m.direction_good, cf.value, cf.prior_value, cf.mom_pct,
                       cf.yoy_pct, cf.budget_var_abs, cf.budget_var_pct, cf.trend, cf.is_anomaly
                FROM metrics m
                LEFT JOIN computed_facts cf ON cf.metric_id = m.id AND cf.period = ?
                WHERE m.dataset_id = ?
                ORDER BY ABS(COALESCE(cf.budget_var_abs, 0)) DESC, m.name
                """,
                (period, ds),
            ).fetchall()

        out = []
        for r in rows:
            has_data = r["metric_id"] is not None and r["value"] is not None
            report = None
            if has_data:
                report = conn.execute(
                    """
                    SELECT gr.id, gr.narrative, gr.sources, gr.confidence, gr.faithfulness,
                           gr.cost_usd, gr.latency_ms, gr.user_email,
                           (SELECT edited_text FROM feedback
                            WHERE report_id = gr.id AND action = 'edited' AND edited_text IS NOT NULL
                            ORDER BY id DESC LIMIT 1) AS edited_text,
                           (SELECT user_email FROM feedback
                            WHERE report_id = gr.id AND user_email IS NOT NULL
                            ORDER BY id DESC LIMIT 1) AS reviewer_email
                    FROM generated_reports gr
                    WHERE gr.metric_id = ? AND gr.period = ?
                      AND (gr.prompt_version IS NULL OR gr.prompt_version NOT LIKE '%-digest')
                    ORDER BY gr.id DESC LIMIT 1
                    """,
                    (r["metric_id"], period),
                ).fetchone()
            edited = report["edited_text"] if report else None
            out.append({
                "metric": r["metric"],
                "category": r["category"],
                "period": period,
                "value": r["value"],
                "unit": r["unit"],
                "direction_good": r["direction_good"],
                "prior_value": r["prior_value"],
                "has_data": has_data,
                "deltas": {
                    "mom_pct": r["mom_pct"], "yoy_pct": r["yoy_pct"],
                    "budget_var_abs": r["budget_var_abs"], "budget_var_pct": r["budget_var_pct"],
                },
                "trend": r["trend"],
                "is_anomaly": bool(r["is_anomaly"]) if r["is_anomaly"] is not None else False,
                "report_id": report["id"] if report else None,
                "narrative": (edited or report["narrative"]) if report else None,
                "original_narrative": report["narrative"] if (report and edited) else None,
                "edited": edited is not None,
                "sources": json.loads(report["sources"]) if (report and report["sources"]) else [],
                "confidence": report["confidence"] if report else None,
                "faithfulness": report["faithfulness"] if report else None,
                "cost_usd": report["cost_usd"] if report else None,
                "latency_ms": report["latency_ms"] if report else None,
                "generated_by": report["user_email"] if report else None,
                "reviewed_by": report["reviewer_email"] if report else None,
                "chart_data": (
                    _chart_data(conn, r["metric_id"], r["metric"], period)
                    if include_charts and has_data else None
                ),
                "trend_streak": (
                    detect_consecutive_trends(conn, r["metric_id"], period)
                    if has_data else None
                ),
            })
        return out
    finally:
        conn.close()


@app.post("/ingest", response_model=IngestSummary)
async def ingest(file: UploadFile, user: CurrentUser = Depends(require_write)) -> IngestSummary:
    """Shortcut ingest for files already in the canonical format
    (period, metric, value, budget). Arbitrary layouts go through the
    flexible flow: /ingest/upload -> schema -> mapping."""
    raw = await file.read()
    try:
        df = parse_csv(raw)
    except IngestValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conn = get_connection()
    try:
        dataset_id = create_dataset(conn, name=file.filename or "CSV import",
                                    uploaded_by=user.id, uploaded_by_email=user.email)
        summary = ingest_dataframe(conn, df, dataset_id)
        compute_and_store(conn, dataset_id)
        _notify_anomalies(conn, dataset_id)
    finally:
        conn.close()
    shared_cache().bump_namespace()  # new data invalidates cached narratives
    return summary


# ---------- Flexible ingestion (Addendum v1.1: detect -> map -> pick) ----------


@app.post("/ingest/upload")
async def ingest_upload(file: UploadFile, _: CurrentUser = Depends(require_write)) -> dict:
    raw = await file.read()
    conn = get_connection()
    try:
        return create_upload(conn, raw, file.filename or "upload.csv")
    except UploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()


@app.get("/ingest/{upload_id}/schema")
def ingest_schema(upload_id: str, sheet: str | None = None, _: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        df = load_upload_frame(conn, upload_id, sheet)
        profile = profile_columns(df)
        if sheet:
            conn.execute("UPDATE uploads SET chosen_sheet = ? WHERE id = ?", (sheet, upload_id))
        conn.execute(
            "UPDATE uploads SET profiled_schema = ? WHERE id = ?",
            (json.dumps(profile), upload_id),
        )
        conn.commit()
        return profile
    except UploadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/ingest/{upload_id}/mapping")
def ingest_mapping(upload_id: str, mapping: MappingSpec, save_template: bool = False,
                   user: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        df = load_upload_frame(conn, upload_id)
        try:
            canonical = normalize(df, mapping)
        except MappingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        up = conn.execute(
            "SELECT filename, column_signature FROM uploads WHERE id = ?", (upload_id,)
        ).fetchone()
        # A mapping confirmation is a new dataset: its metrics/facts are scoped
        # to it so it never mixes with earlier uploads on the dashboard.
        dataset_id = create_dataset(
            conn, name=up["filename"] if up else upload_id, source_upload_id=upload_id,
            uploaded_by=user.id, uploaded_by_email=user.email,
        )
        summary = store_normalized(conn, canonical, dataset_id)
        conn.execute(
            "UPDATE uploads SET mapping = ? WHERE id = ?",
            (mapping.model_dump_json(), upload_id),
        )
        conn.commit()

        template_id = None
        if save_template:
            template_id = save_mapping_template(conn, up["column_signature"], mapping.model_dump())

        facts = compute_and_store(conn, dataset_id)
        _notify_anomalies(conn, dataset_id)
        shared_cache().bump_namespace()
        return {**summary, "dataset_id": dataset_id,
                "facts_computed": int(len(facts)), "template_id": template_id}
    except UploadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@app.get("/ingest/{upload_id}/metrics")
def ingest_metrics(upload_id: str, _: CurrentUser = Depends(require_write)) -> list[dict]:
    """Distinct metric names in this upload's normalized data, each with a
    KPI-library suggestion for auto-filling category/unit/direction."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT mapping FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found")
        if not row["mapping"]:
            raise HTTPException(status_code=409, detail="Confirm a mapping first")
        mapping = MappingSpec(**json.loads(row["mapping"]))
        df = load_upload_frame(conn, upload_id)
        canonical = normalize(df, mapping)
        return [
            {"source_metric": m, "suggestion": suggest_kpi(m)}
            for m in sorted(canonical["metric"].unique())
        ]
    finally:
        conn.close()


@app.get("/templates/{template_id}")
def get_mapping_template(template_id: int, _: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        tpl = load_template(conn, template_id)
    finally:
        conn.close()
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
    return tpl


@app.post("/kpis")
def configure_kpis(payload: KPIConfigPayload, _: CurrentUser = Depends(require_write)) -> dict:
    """Persist KPI selections and apply them to the metrics table so compute,
    generation, and the UI all see display name, category, unit, and
    direction_good (down = lower is better, e.g. OpEx)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=409, detail="No active dataset — import data first")
        for kpi in payload.kpis:
            conn.execute(
                """
                INSERT INTO kpi_configs (dataset_id, source_metric, display_name, category, unit, direction_good, budget_source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_id, source_metric) DO UPDATE SET
                    display_name=excluded.display_name, category=excluded.category,
                    unit=excluded.unit, direction_good=excluded.direction_good,
                    budget_source=excluded.budget_source
                """,
                (ds, kpi.source_metric, kpi.display_name, kpi.category, kpi.unit,
                 kpi.direction_good, kpi.budget_source),
            )
            conn.execute(
                "UPDATE metrics SET category = ?, unit = ?, direction_good = ? WHERE name = ? AND dataset_id = ?",
                (kpi.category, kpi.unit, kpi.direction_good, kpi.source_metric, ds),
            )
        conn.commit()
    finally:
        conn.close()
    shared_cache().bump_namespace()
    return {"kpis_configured": len(payload.kpis)}


@app.get("/kpis")
def list_kpis(_: CurrentUser = Depends(require_read)) -> dict:
    """Current KPI selections for the active dataset (drives the board), plus
    the dataset's metrics that are NOT selected (so the UI can add them back)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return {"selected": [], "available": []}
        selected = [dict(r) for r in conn.execute(
            """SELECT id, source_metric, display_name, category, unit, direction_good
               FROM kpi_configs WHERE dataset_id = ? ORDER BY source_metric""",
            (ds,),
        ).fetchall()]
        chosen = {s["source_metric"] for s in selected}
        available = [r["name"] for r in conn.execute(
            "SELECT name FROM metrics WHERE dataset_id = ? ORDER BY name", (ds,)
        ).fetchall() if r["name"] not in chosen]
        return {"selected": selected, "available": available}
    finally:
        conn.close()


@app.delete("/kpis/{kpi_id}", status_code=204)
def delete_kpi(kpi_id: int, _: CurrentUser = Depends(require_write)) -> None:
    """Remove one KPI from the board (its data stays; it's just deselected)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        conn.execute("DELETE FROM kpi_configs WHERE id = ? AND dataset_id = ?", (kpi_id, ds))
        conn.commit()
    finally:
        conn.close()
    shared_cache().bump_namespace()


@app.get("/kpis/library")
def kpi_library(domain: str | None = None, _: CurrentUser = Depends(require_read)) -> list[dict]:
    """KPI suggestions. Defaults to FP&A; pass ?domain= for a domain's library."""
    if domain and registry.has(domain):
        return get_domain(domain).kpi_library
    return [{k: v for k, v in kpi.items() if k != "match"} for kpi in KPI_LIBRARY]


@app.post("/compute")
def compute(_: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        facts = compute_and_store(conn, ds)
        if ds is not None:
            _notify_anomalies(conn, ds)
    finally:
        conn.close()
    shared_cache().bump_namespace()
    return {"facts_computed": int(len(facts))}


def _active_metric_id(conn, metric: str) -> int | None:
    ds = active_dataset_id(conn)
    if ds is None:
        return None
    row = conn.execute(
        "SELECT id FROM metrics WHERE name = ? AND dataset_id = ?", (metric, ds)
    ).fetchone()
    return int(row["id"]) if row else None


def _load_bridge(conn, metric: str, period: str) -> dict | None:
    """Prefer the stored multi-item P/V/M bridge (from dimension-level detail);
    fall back to an on-the-fly single-row bridge for simple qty/price data.
    Scoped to the active dataset's metric."""
    metric_id = _active_metric_id(conn, metric)
    if metric_id is None:
        return None
    row = conn.execute(
        "SELECT volume, price, mix FROM pvm_bridges WHERE metric_id = ? AND period = ?",
        (metric_id, period),
    ).fetchone()
    if row is not None:
        return {"volume": row["volume"], "price": row["price"], "mix": row["mix"]}

    mv = conn.execute(
        """
        SELECT quantity, budget_quantity, price, budget_price
        FROM metric_values WHERE metric_id = ? AND period = ?
        """,
        (metric_id, period),
    ).fetchone()
    if mv:
        vb = bridge_for_metric_row(
            mv["quantity"], mv["budget_quantity"], mv["price"], mv["budget_price"]
        )
        if vb is not None:
            return {"volume": vb.volume, "price": vb.price, "mix": vb.mix}
    return None


def _load_computed_fact(conn, metric: str, period: str) -> ComputedFact:
    metric_id = _active_metric_id(conn, metric)
    row = None
    if metric_id is not None:
        row = conn.execute(
            """
            SELECT m.name AS metric, m.category, m.unit, cf.period, cf.value, cf.prior_value,
                   cf.mom_pct, cf.yoy_pct, cf.budget_var_abs, cf.budget_var_pct, cf.trend, cf.is_anomaly
            FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
            WHERE cf.metric_id = ? AND cf.period = ?
            """,
            (metric_id, period),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No computed facts for metric='{metric}' period='{period}'. Run /compute first.",
        )
    bridge = _load_bridge(conn, metric, period)

    return ComputedFact(
        metric=row["metric"],
        category=row["category"],
        period=row["period"],
        value=row["value"],
        unit=row["unit"],
        prior_value=row["prior_value"],
        deltas=Deltas(
            mom_pct=row["mom_pct"],
            yoy_pct=row["yoy_pct"],
            budget_var_abs=row["budget_var_abs"],
            budget_var_pct=row["budget_var_pct"],
        ),
        trend=row["trend"],
        is_anomaly=bool(row["is_anomaly"]),
        variance_bridge=bridge,
    )


def _context_store(conn) -> ContextStore:
    return ContextStore(conn, shared_embedder(), shared_vector_store())


@app.post("/context", response_model=ContextDoc, status_code=201)
def add_context(doc: ContextDocIn, user: CurrentUser = Depends(require_write)) -> ContextDoc:
    conn = get_connection()
    try:
        added = _context_store(conn).add(doc)
        conn.execute(
            "UPDATE context_documents SET created_by = ?, updated_by = ? WHERE id = ?",
            (user.id, user.id, added.id),
        )
        conn.commit()
    finally:
        conn.close()
    shared_cache().bump_namespace()  # context changes change retrieval results
    return added


@app.get("/context", response_model=list[ContextDoc])
def list_context(_: CurrentUser = Depends(require_read)) -> list[ContextDoc]:
    conn = get_connection()
    try:
        return _context_store(conn).list()
    finally:
        conn.close()


@app.put("/context/{doc_id}", response_model=ContextDoc)
def update_context(doc_id: int, doc: ContextDocIn, user: CurrentUser = Depends(require_write)) -> ContextDoc:
    conn = get_connection()
    try:
        updated = _context_store(conn).update(doc_id, doc)
        if updated is not None:
            conn.execute(
                "UPDATE context_documents SET updated_by = ? WHERE id = ?", (user.id, doc_id)
            )
            conn.commit()
    finally:
        conn.close()
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Context document {doc_id} not found")
    shared_cache().bump_namespace()
    return updated


@app.delete("/context/{doc_id}", status_code=204)
def delete_context(doc_id: int, _: CurrentUser = Depends(require_write)) -> None:
    conn = get_connection()
    try:
        deleted = _context_store(conn).delete(doc_id)
    finally:
        conn.close()
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Context document {doc_id} not found")
    shared_cache().bump_namespace()


@app.post("/generate-insight", response_model=InsightOutput)
def generate_insight_endpoint(req: GenerateInsightRequest, user: CurrentUser = Depends(require_read)) -> InsightOutput:
    conn = get_connection()
    try:
        fact = _load_computed_fact(conn, req.metric, req.period)

        # Enriched deterministic analysis (v2.1): correlated metrics + trend streak.
        correlations: list[CorrelationPair] = []
        trend_streak: TrendStreak | None = None
        domain_slug = "fpa"
        ds = active_dataset_id(conn)
        if ds is not None:
            drow = conn.execute("SELECT domain FROM datasets WHERE id = ?", (ds,)).fetchone()
            if drow and drow["domain"]:
                domain_slug = drow["domain"]
            correlations = [
                CorrelationPair(**c)
                for c in correlations_for_metric(conn, ds, req.metric, req.period)
            ]
            mrow = conn.execute(
                "SELECT id FROM metrics WHERE dataset_id = ? AND name = ?", (ds, req.metric)
            ).fetchone()
            if mrow is not None:
                streak = detect_consecutive_trends(conn, mrow["id"], req.period)
                if streak is not None:
                    trend_streak = TrendStreak(**streak)

        context = req.context
        retrieval_scores: list[float] | None = None
        if not context and req.use_retrieval:
            # Retrieval (query embedding + vector search) is itself cached,
            # namespace-scoped: any context/data change invalidates it.
            retr_key = f"retrieval:{req.metric}:{req.period}:k{settings.retrieval_k}"
            cached_retr = shared_cache().get(retr_key)
            if cached_retr is not None:
                context = [ContextSnippet(**c) for c in cached_retr["context"]]
                retrieval_scores = cached_retr["scores"]
            else:
                chunks = retrieve(
                    req.metric,
                    req.period,
                    _context_store(conn),
                    shared_embedder(),
                    shared_vector_store(),
                    k=settings.retrieval_k,
                )
                context = [
                    ContextSnippet(
                        id=f"ctx_{c.id:03d}", type=c.type, title=c.title, body=c.body
                    )
                    for c in chunks
                ]
                retrieval_scores = [c.score for c in chunks]
                shared_cache().set(
                    retr_key,
                    {"context": [c.model_dump() for c in context], "scores": retrieval_scores},
                    settings.cache_ttl_seconds,
                )
    finally:
        conn.close()

    # v1.1 Phase 3: identical requests are served from cache unless forced.
    cache = shared_cache()
    cache_key = make_insight_key(
        req.metric,
        req.period,
        fact.model_dump(),
        [(c.id, c.title, c.body) for c in context],
        f"{PROMPT_VERSION}:{domain_slug}",   # domain changes the system prompt
        settings.llm_provider,
        settings.openai_model if settings.llm_provider == "openai" else settings.anthropic_model,
    )
    if not req.force:
        hit = cache.get(cache_key)
        if hit is not None:
            cache.record(hit=True)
            return InsightOutput(**{**hit, "cached": True})
    cache.record(hit=False)

    try:
        llm_client = get_llm_client()
        insight = generate_insight(
            fact, context, llm_client, retrieval_scores,
            embedder_semantic=getattr(shared_embedder(), "semantic", True),
            correlations=correlations, trend_streak=trend_streak,
            system_prompt=get_domain(domain_slug).system_prompt,
        )
    except GenerationFailedFactsOnly as exc:
        return JSONResponse(status_code=503, content=exc.facts_only.model_dump())
    except LLMGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    insight.generated_by = user.email
    insight.report_id = _persist_report(insight, user)
    _log_llm_call(
        "/generate-insight",
        settings.openai_model if settings.llm_provider == "openai" else settings.anthropic_model,
        insight.prompt_tokens, insight.completion_tokens, insight.cost_usd, insight.latency_ms,
        user.id,
    )
    cache.set(cache_key, insight.model_dump(), settings.cache_ttl_seconds)
    return insight


def _persist_report(insight: InsightOutput, user: CurrentUser) -> int:
    """Audit trail (PRD Section 8): every report logs which context ids fed it,
    plus token usage, cost, latency, prompt version, and who generated it."""
    conn = get_connection()
    try:
        metric_id = _active_metric_id(conn, insight.metric)
        if metric_id is None:
            return None
        cur = conn.execute(
            """
            INSERT INTO generated_reports (metric_id, period, narrative, sources,
                        confidence, faithfulness, prompt_tokens, completion_tokens,
                        cost_usd, latency_ms, prompt_version, user_id, user_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metric_id,
                insight.period,
                insight.narrative,
                json.dumps([s.model_dump() for s in insight.sources]),
                insight.confidence,
                insight.faithfulness,
                insight.prompt_tokens,
                insight.completion_tokens,
                insight.cost_usd,
                insight.latency_ms,
                insight.prompt_version,
                user.id,
                user.email,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


@app.post("/ask")
def ask_endpoint(payload: dict, user: CurrentUser = Depends(require_read)) -> dict:
    """Grounded Q&A about one metric (v2.9). Same guarantees as narratives:
    numbers only from computed facts, causes only from retrieved context, and
    an honest "the data doesn't answer that" when neither covers the question."""
    metric = (payload.get("metric") or "").strip()
    period = (payload.get("period") or "").strip()
    question = (payload.get("question") or "").strip()
    if not metric or not period or not question:
        raise HTTPException(status_code=422, detail="metric, period, and question are required")
    if len(question) > 500:
        raise HTTPException(status_code=422, detail="Question too long (max 500 chars)")

    conn = get_connection()
    try:
        fact = _load_computed_fact(conn, metric, period)
        chunks = retrieve(metric, period, _context_store(conn),
                          shared_embedder(), shared_vector_store(), k=settings.retrieval_k)
        context = [ContextSnippet(id=f"ctx_{c.id:03d}", type=c.type, title=c.title, body=c.body)
                   for c in chunks]
    finally:
        conn.close()

    try:
        llm_client = get_llm_client()
        result, usage = llm_client.generate_narrative(
            QA_SYSTEM_PROMPT, build_qa_prompt(fact, context, question)
        )
    except LLMGenerationError as exc:
        raise HTTPException(status_code=503, detail=f"Q&A unavailable: {exc}") from exc

    passed, _unverified = check_faithfulness(result.narrative, fact, context)
    context_by_id = {c.id: c for c in context}
    sources = [
        {"id": cid, "type": context_by_id[cid].type, "title": context_by_id[cid].title}
        for cid in result.sources_used if cid in context_by_id
    ]
    _log_llm_call(
        "/ask",
        settings.openai_model if settings.llm_provider == "openai" else settings.anthropic_model,
        usage.prompt_tokens, usage.completion_tokens, usage.cost_usd, None, user.id,
    )
    return {"answer": result.narrative, "sources": sources, "grounded": passed}


@app.post("/digest", response_model=DigestOutput)
def digest_endpoint(period: str, top_n: int = 5, force: bool = False,
                    user: CurrentUser = Depends(require_read)) -> DigestOutput:
    cache = shared_cache()
    cache_key = f"digest:{period}:top{top_n}:{PROMPT_VERSION}"
    if not force:
        hit = cache.get(cache_key)
        if hit is not None:
            cache.record(hit=True)
            return DigestOutput(**{**hit, "cached": True})
    cache.record(hit=False)

    conn = get_connection()
    try:
        llm_client = get_llm_client()
        digest = generate_digest(conn, period, llm_client, top_n)
    except LLMGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()

    _log_llm_call("/digest", None, None, None, digest.cost_usd, None, user.id)
    cache.set(cache_key, digest.model_dump(), settings.cache_ttl_seconds)
    # Fan the fresh digest out to subscribed channels (email/Slack/webhook).
    _notify_async("digest_generated", period=period, items=[{
        "metric": it.metric, "period": period,
        "value": it.headline,
        "delta": f"{it.budget_var_pct:+.1f}% vs plan" if it.budget_var_pct is not None else "",
        "narrative": it.detail,
    } for it in digest.items])
    return digest


@app.post("/feedback", status_code=201)
def feedback_endpoint(fb: FeedbackIn, user: CurrentUser = Depends(require_read)) -> dict:
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM generated_reports WHERE id = ?", (fb.report_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Report {fb.report_id} not found")
        cur = conn.execute(
            """INSERT INTO feedback (report_id, action, edited_text, reason, user_id, user_email)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (fb.report_id, fb.action, fb.edited_text, fb.reason, user.id, user.email),
        )
        conn.commit()
        return {"feedback_id": int(cur.lastrowid)}
    finally:
        conn.close()


# ---------- Admin: user management (v1.2 Phase 2) ----------


@app.get("/admin/users")
def admin_list_users(_: CurrentUser = Depends(require_admin)) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT user_id, email, role, created_at, updated_at FROM app_roles ORDER BY created_at"
        ).fetchall()
        return [
            {"user_id": str(r["user_id"]), "email": r["email"], "role": r["role"],
             "created_at": str(r["created_at"]), "updated_at": str(r["updated_at"])}
            for r in rows
        ]
    finally:
        conn.close()


@app.put("/admin/users/{user_id}/role")
def admin_set_role(user_id: str, role: str, current: CurrentUser = Depends(require_admin)) -> dict:
    from app.auth import VALID_ROLES

    if role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {VALID_ROLES}")
    if user_id == current.id and role != "admin":
        raise HTTPException(status_code=400, detail="You cannot demote yourself")
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM app_roles WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        conn.execute(
            "UPDATE app_roles SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (role, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    invalidate_role_cache(user_id)  # so the change takes effect immediately
    return {"user_id": user_id, "role": role}


@app.get("/me")
def whoami(user: CurrentUser = Depends(get_current_user)) -> dict:
    """The current user's identity + role, for the frontend header/badge."""
    return {"id": user.id, "email": user.email, "role": user.role}
