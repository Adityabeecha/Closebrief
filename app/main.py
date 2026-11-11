import hmac
import json
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app import audit, connectors, review, scheduling, workspaces
from app.auth import CurrentUser, authenticate, get_current_user
from app.board_pack import build_board_pack_html
from app.cache import make_insight_key
from app.compute import derived as derived_metrics
from app.compute.aggregate import (
    GRANULARITIES,
    aggregate_series,
    infer_aggregation_type,
    prior_key,
    sort_key,
    year_ago_key,
)
from app.compute.availability import has_usable_value
from app.compute.correlations import (
    compare_periods,
    compute_correlations,
    correlations_for_metric,
    detect_consecutive_trends,
    streak_from_history,
)
from app.compute.funnel import compute_funnel
from app.compute.kpis import compute_and_store
from app.compute.pvm import bridge_for_metric_row
from app.config import auth_active, check_production_auth, google_enabled, resolved_vector_backend, settings
from app.context.conflicts import find_conflicts
from app.context.store import ContextStore
from app.datasets import (
    _scope_pred,
    active_dataset_id,
    current_workspace,
    dataset_currency,
    is_demo_scope,
    set_dataset_choice,
    set_demo_scope,
    set_workspace_scope,
)
from app.db import get_connection, init_db
from app.demo import seed_demo
from app.deps import shared_cache, shared_embedder, shared_vector_store
from app.digest.digest import DigestOutput, build_approved_digest, last_sent_at
from app.domains import get_domain, list_domains, registry
from app.generation.generate import GenerationFailedFactsOnly, generate_insight
from app.generation.llm_client import LLMGenerationError, get_llm_client
from app.generation.prompts import (
    FUNNEL_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_funnel_prompt,
)
from app.generation.qa import answer_question
from app.google_auth import GoogleAuthError, sign_in_with_google
from app.grounding import attribute
from app.retrieval.retrieve import retrieve
from app.routes_common import (
    _ALERT_LATENCIES,
    _notify_anomalies,
    _notify_async,
)
from app.scheduling import record_digest_run
from app.schemas import (
    ComputedFact,
    ContextDoc,
    ContextDocIn,
    ContextSnippet,
    CorrelationPair,
    Deltas,
    FeedbackIn,
    GenerateInsightRequest,
    InsightOutput,
    TrendStreak,
)
from app.workspaces import resolve_workspace

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

logger = logging.getLogger("closebrief")


@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    """Startup: create/migrate schema, add the hot /facts index, and (in demo
    mode) seed the sample dataset off-thread. Failures in the optional steps are
    logged, never fatal — boot and the health check must not be blocked.
    The one fatal check is production auth with no sign-in provider."""
    check_production_auth()
    try:
        init_db()
    except Exception:  # noqa: BLE001
        logger.exception("init_db failed at startup — continuing in degraded mode")
        app.state.init_db_error = True
    # Index the hot lookup in /facts (feedback by report). IF NOT EXISTS is
    # portable across SQLite and Postgres and idempotent on every boot.
    try:
        conn = get_connection()
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_report ON feedback(report_id)")
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - index is an optimization, never block boot
        logger.warning("could not create idx_feedback_report", exc_info=True)
    if settings.demo_mode:
        # Seed the sample dataset on a background thread — embedding the demo
        # context docs takes a few seconds and must not delay boot / the health
        # check (idempotent, so a restart mid-seed is safe).
        import threading

        def _seed():
            try:
                conn = get_connection()
                try:
                    seed_demo(conn, _context_store(conn))
                finally:
                    conn.close()
            except Exception:  # noqa: BLE001 - must never block startup, but must be visible
                logger.exception("demo seed failed")

        threading.Thread(target=_seed, daemon=True).start()
    yield


# The interactive API docs are for development; production doesn't publish its endpoint map.
_PUBLIC_DOCS = settings.environment != "prod"

app = FastAPI(
    title="Closebrief",
    description="RAG-based AI agent for automated FP&A variance commentary. "
    "The LLM never computes numbers — see app/compute/kpis.py for the deterministic layer.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _PUBLIC_DOCS else None,
    redoc_url="/redoc" if _PUBLIC_DOCS else None,
    openapi_url="/openapi.json" if _PUBLIC_DOCS else None,
)

# CORS: the frontend authenticates against Supabase directly (cross-origin) and
# calls this API with a Bearer token (no cookies), so allow-any-origin is safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Role-guard dependencies live in app.api so routers share one definition.
from app.api import require_admin, require_member, require_read, require_write  # noqa: E402
from app.services import enforce_budget as _enforce_budget  # noqa: E402
from app.services import log_llm_call as _log_llm_call  # noqa: E402

# Paths reachable without a token: the app shell, health, the public auth config
# the frontend needs to boot the login form, and the API docs.
# /app.js is the shell's own script (split out of index.html): a browser cannot
# attach a bearer token to <script src>, so gating it 401s the entire frontend.
_OPEN_PATHS = {"/", "/app", "/theme.css", "/app.js", "/health", "/auth/config", "/auth/google", "/openapi.json", "/docs", "/redoc",
               "/samples/fpa.csv", "/og.png"}


# ---- request telemetry: latency ring buffer + naive per-IP rate limit ----
# NOTE: these + the auth role cache (app/auth.py) are per-process, in-memory.
# That's correct on the single-process free tier, but if this ever runs multiple
# workers they won't share state — the rate limit would be per-worker and a role
# change could take up to the cache TTL to propagate. Move to Redis before scaling out.
_LATENCIES: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
_RATE_BUCKETS: dict[str, deque] = defaultdict(deque)
_RATE_LIMIT_PER_MINUTE = 240  # generous: the UI's generate-all bursts stay well under
# Bounds so request-spray (random paths / many IPs) can't grow these unboundedly.
_MAX_LATENCY_PATHS = 200
_last_bucket_sweep = 0.0


def _sweep_rate_buckets(now: float) -> None:
    """Drop visitor buckets with no requests in the last minute, at most once a minute,
    so _RATE_BUCKETS stays proportional to *recently active* visitors, not all-time."""
    global _last_bucket_sweep
    if now - _last_bucket_sweep < 60:
        return
    _last_bucket_sweep = now
    for ip in [ip for ip, b in _RATE_BUCKETS.items() if not b or now - b[-1] > 60]:
        del _RATE_BUCKETS[ip]


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    """Authentication gate (401). Attaches CurrentUser to request.state so
    downstream role guards (403) and attribution can read it. Bypassed for the
    open paths and, entirely, when auth is not active (local dev)."""
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or path in _OPEN_PATHS
        or (_PUBLIC_DOCS and path.startswith("/docs"))
        or path.startswith("/vendor/")   # static frontend libs, needed pre-login
        or path.startswith("/internal/")  # cron endpoints: gated by their own shared token
    ):
        return await call_next(request)
    try:
        user = authenticate(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    request.state.user = user
    requested_ds = request.headers.get("x-dataset-id")
    try:
        set_dataset_choice(int(requested_ds) if requested_ds else None)
    except ValueError:
        set_dataset_choice(None)
    # Scope every dataset query to the caller's universe: demo (viewer) sessions
    # see only the demo dataset; everyone else sees only real datasets.
    is_demo = user.role == "viewer"
    set_demo_scope(is_demo)
    # v4.0 tenancy: for a real, authenticated user, resolve the active workspace
    # from verified membership (X-Workspace-Id is validated, never trusted blindly)
    # and scope all data to it. Skipped for demo and for local-dev (auth off), which
    # stay single-tenant. Failure to resolve leaves scope unset (fail-closed on data
    # is handled by endpoints returning empty for an unknown workspace).
    set_workspace_scope(None)
    if auth_active() and not is_demo:
        requested = request.headers.get("x-workspace-id")
        try:
            req_ws = int(requested) if requested else None
        except ValueError:
            req_ws = None
        ws_id, ws_role = resolve_workspace(None, user.id, user.email, req_ws, connection_factory=get_connection)
        set_workspace_scope(ws_id)
        request.state.workspace_id = ws_id
        # Membership changes invalidate the cached per-workspace role immediately.
        request.state.ws_role = ws_role
    return await call_next(request)


def _content_security_policy() -> str:
    """Where pages may load from: this site, plus Google sign-in, Supabase auth and
    Sentry when configured. 'unsafe-inline' is still needed because the UI uses
    inline handlers and styles; the rest of the policy limits what an injected
    script could reach (no foreign scripts, frames, or data exfiltration hosts)."""
    connect = ["'self'"]
    if settings.supabase_url:
        connect.append(settings.supabase_url.rstrip("/"))
    if settings.sentry_dsn:
        connect.append("https://*.sentry.io")
    google = "https://accounts.google.com"
    if settings.google_client_id:
        connect.append(google)
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'unsafe-inline' {google}",
        f"style-src 'self' 'unsafe-inline' {google}",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src " + " ".join(connect),
        f"frame-src 'self' {google}",
        "frame-ancestors 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ])


@app.middleware("http")
async def _security_headers(request, call_next):
    """Standard browser protections on every response."""
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    h.setdefault("Content-Security-Policy", _content_security_policy())
    if settings.environment == "prod":
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.middleware("http")
async def _telemetry_middleware(request, call_next):
    import hashlib

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        visitor_key = "token:" + hashlib.sha256(authorization.encode()).hexdigest()
    else:
        client_ip = request.client.host if request.client else "?"
        if settings.trust_proxy_headers:
            # The visitor can write anything at the START of X-Forwarded-For; each
            # proxy appends what it saw at the END. Cloudflare's own header can't
            # be set by the visitor, so prefer it, else take the last hop.
            hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
            client_ip = request.headers.get("cf-connecting-ip", "").strip() or (hops[-1] if hops else client_ip)
        visitor_key = "ip:" + client_ip
    now = time.time()
    _sweep_rate_buckets(now)
    bucket = _RATE_BUCKETS[visitor_key]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_PER_MINUTE:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    bucket.append(now)

    t0 = time.perf_counter()
    response = await call_next(request)
    # Only record latency for real (matched) routes, and cap distinct paths so a
    # 404-spray of random URLs can't grow the dict without bound.
    path = request.url.path
    if response.status_code != 404 and (path in _LATENCIES or len(_LATENCIES) < _MAX_LATENCY_PATHS):
        _LATENCIES[path].append((time.perf_counter() - t0) * 1000)
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
        # Google sign-in (optional). The client id is designed to be embedded in
        # a web page, so exposing it is safe. Empty + google_enabled false means
        # the frontend never loads Google's script at all.
        "google_client_id": settings.google_client_id if google_enabled() else "",
        "google_enabled": google_enabled(),
        "allow_guest": settings.allow_guest,
        "auth_required": auth_active() and not settings.allow_guest,
    }


@app.post("/auth/google")
def auth_google(payload: dict) -> dict:
    """Exchange a Google ID token for a Closebrief session token.

    Open (the caller has no token yet) and rate-limited by the telemetry
    middleware. The credential is attacker-supplied: every failure path returns
    401 with a readable reason, never a stack trace."""
    if not google_enabled():
        raise HTTPException(status_code=401,
                            detail="Google sign-in is not configured on this server")
    credential = (payload or {}).get("credential")
    try:
        result = sign_in_with_google(credential)
    except GoogleAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    conn = get_connection()
    try:
        audit.record(conn, "login", "user", result["email"], actor_email=result["email"],
                     summary={"provider": "google", "role": result["role"]})
    except Exception:  # noqa: BLE001 - audit must never block a successful login
        pass
    finally:
        conn.close()
    return result


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))
    return round(ordered[idx], 1)


# Alert latency samples (ms from anomaly detection to delivery completion), so
# the "< 30s from compute" success criterion is measurable in the Cost page.


# Vendored frontend libraries served from our own origin (no CDN dependency, so
# the app works offline / behind ad-blockers / strict networks).


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
def costs(_: CurrentUser = Depends(require_admin)) -> dict:
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
    alert_samples = list(_ALERT_LATENCIES)
    alert_latency = {
        "p50_ms": _percentile(alert_samples, 50) if alert_samples else None,
        "p95_ms": _percentile(alert_samples, 95) if alert_samples else None,
        "n": len(alert_samples),
        # The success criterion: anomaly alert reaches channels < 30s from compute.
        "within_30s": (max(alert_samples) <= 30000) if alert_samples else None,
    }
    return {
        "total_llm_calls": total["calls"] or 0,
        "total_cost_usd": round(total["cost_usd"] or 0.0, 6),
        "by_day": by_day,
        "latency": latency,
        "alert_latency": alert_latency,
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
        return {**result, "currency": dataset_currency(conn, ds)}
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
        return {**get_domain(slug).public(), "currency": dataset_currency(conn, ds)}
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
        return {**get_domain(slug).public(), "currency": dataset_currency(conn, ds)}
    finally:
        conn.close()


def _active_domain_slug(conn) -> str:
    ds = active_dataset_id(conn)
    if ds is not None:
        row = conn.execute("SELECT domain FROM datasets WHERE id = ?", (ds,)).fetchone()
        if row and row["domain"]:
            return row["domain"]
    return "fpa"


@app.get("/funnel")
def get_funnel(period: str, _: CurrentUser = Depends(require_read)) -> dict:
    """Stage-over-stage funnel for the active dataset, if its domain defines one
    (Phase 1). Deterministic — the LLM is not involved."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=400, detail="No active dataset")
        domain = get_domain(_active_domain_slug(conn))
        if not domain.funnel:
            raise HTTPException(status_code=400,
                                detail=f"The {domain.name} domain has no funnel defined")
        return compute_funnel(conn, ds, period, domain.funnel)
    finally:
        conn.close()


@app.post("/funnel/narrative")
def funnel_narrative(payload: dict, user: CurrentUser = Depends(require_member)) -> dict:
    """A grounded prose summary of the funnel — numbers computed here, phrased by
    the LLM under the funnel prompt."""
    _enforce_budget(user)
    period = (payload.get("period") or "").strip()
    if not period:
        raise HTTPException(status_code=422, detail="period is required")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=400, detail="No active dataset")
        domain = get_domain(_active_domain_slug(conn))
        if not domain.funnel:
            raise HTTPException(status_code=400, detail="No funnel for this domain")
        funnel = compute_funnel(conn, ds, period, domain.funnel)
    finally:
        conn.close()
    if len([s for s in funnel["stages"] if s["conversion_from_prev"] is not None]) == 0:
        raise HTTPException(status_code=400, detail="Not enough funnel stages with data")
    try:
        result, usage = get_llm_client().generate_narrative(
            FUNNEL_SYSTEM_PROMPT, build_funnel_prompt(funnel)
        )
    except LLMGenerationError as exc:
        raise HTTPException(status_code=503, detail=f"Funnel narrative unavailable: {exc}") from exc
    _log_llm_call(
        "/funnel/narrative",
        settings.openai_model if settings.llm_provider == "openai" else settings.anthropic_model,
        usage.prompt_tokens, usage.completion_tokens, usage.cost_usd, None, user.id,
    )
    return {"funnel": funnel, "narrative": result.narrative}


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
    current = trend_rows[0] if trend_rows and trend_rows[0]["period"] == period else None
    return _chart_from_rows(trend_rows, current, _load_bridge(conn, metric_name, period))


def _chart_from_rows(history_desc, current, vb: dict | None) -> dict:
    """Build chart_data from a metric's metric_values rows (newest first) and its
    already-resolved P/V/M bridge."""
    trend = [
        {"period": r["period"], "value": r["value"], "budget": r["budget"]}
        for r in reversed(history_desc[:12])
    ]
    budget_vs_actual = (
        {"actual": current["value"], "budget": current["budget"]} if current else None
    )
    bridge = None
    if vb is not None:
        bridge = [
            {"component": "Volume", "impact": vb["volume"]},
            {"component": "Price", "impact": vb["price"]},
            {"component": "Mix", "impact": vb["mix"]},
        ]
    return {"trend": trend, "budget_vs_actual": budget_vs_actual, "variance_bridge": bridge}


def _prefetch_cards(conn, names_by_id: dict[int, str], period: str) -> dict[int, tuple[dict, dict | None]]:
    """chart_data and trend streak for every dashboard card in three queries.
    Per card this was ~7 round trips (trend, current, bridge x3, streak x2),
    which on a remote Postgres made /facts take ~9 s for 7 KPIs."""
    if not names_by_id:
        return {}
    ids = list(names_by_id)
    ph = ",".join("?" * len(ids))
    history: dict[int, list] = {}
    for r in conn.execute(
        f"""SELECT metric_id, period, value, budget, quantity, budget_quantity, price, budget_price
            FROM metric_values WHERE metric_id IN ({ph}) AND period <= ?
            ORDER BY metric_id, period DESC""",
        (*ids, period),
    ).fetchall():
        history.setdefault(r["metric_id"], []).append(r)
    pvm = {
        r["metric_id"]: r
        for r in conn.execute(
            f"SELECT metric_id, volume, price, mix FROM pvm_bridges WHERE metric_id IN ({ph}) AND period = ?",
            (*ids, period),
        ).fetchall()
    }
    moms: dict[int, list] = {}
    for r in conn.execute(
        f"""SELECT metric_id, period, mom_pct FROM computed_facts
            WHERE metric_id IN ({ph}) AND mom_pct IS NOT NULL ORDER BY metric_id, period""",
        tuple(ids),
    ).fetchall():
        moms.setdefault(r["metric_id"], []).append((r["period"], r["mom_pct"]))
    out = {}
    for mid, name in names_by_id.items():
        h = history.get(mid, [])
        current = h[0] if h and h[0]["period"] == period else None
        out[mid] = (
            _chart_from_rows(h, current, _bridge_from_rows(pvm.get(mid), current)),
            streak_from_history(moms.get(mid, []), period, name),
        )
    return out


def _aggregated_facts(conn, ds: int, period: str, granularity: str, include_charts: bool = True) -> list[dict]:
    """Cards for a quarter/year period, aggregated from monthly metric_values
    per each KPI's aggregation rule (flow=SUM, balance=LAST, ratio=unavailable).
    Deltas are QoQ/YoY-equivalent; budget variance uses summed budgets."""
    kpis = conn.execute(
        """SELECT k.source_metric AS metric, k.display_name, k.category, k.unit,
                  k.direction_good, k.aggregation_type, m.id AS metric_id
           FROM kpi_configs k
           LEFT JOIN metrics m ON m.dataset_id = k.dataset_id AND m.name = k.source_metric
           WHERE k.dataset_id = ? ORDER BY k.source_metric""",
        (ds,),
    ).fetchall()
    if not kpis:
        kpis = conn.execute(
            """SELECT name AS metric, name AS display_name, category, unit, direction_good,
                      NULL AS aggregation_type, id AS metric_id
               FROM metrics WHERE dataset_id = ? ORDER BY name""",
            (ds,),
        ).fetchall()

    currency = dataset_currency(conn, ds)
    out = []
    prior = prior_key(period, granularity)
    yago = year_ago_key(period, granularity)
    for k in kpis:
        agg = k["aggregation_type"] or infer_aggregation_type(k["display_name"] or k["metric"], k["unit"])
        rows = []
        if k["metric_id"] is not None:
            rows = [
                {"period": r["period"], "value": r["value"], "budget": r["budget"]}
                for r in conn.execute(
                    "SELECT period, value, budget FROM metric_values WHERE metric_id = ?",
                    (k["metric_id"],),
                ).fetchall()
            ]
        series = aggregate_series(rows, granularity, agg)
        cur = series.get(period)
        has_data = cur is not None and not cur.get("unavailable") and cur.get("value") is not None
        deltas = {"mom_pct": None, "yoy_pct": None, "budget_var_abs": None, "budget_var_pct": None}
        trend = None
        if has_data:
            v = cur["value"]
            pv = (series.get(prior) or {}).get("value")
            yv = (series.get(yago) or {}).get("value")
            if pv:
                deltas["mom_pct"] = round((v - pv) / abs(pv) * 100, 2)
            if yv:
                deltas["yoy_pct"] = round((v - yv) / abs(yv) * 100, 2)
            if cur.get("budget") is not None:
                deltas["budget_var_abs"] = round(v - cur["budget"], 2)
                if cur["budget"]:
                    deltas["budget_var_pct"] = round((v - cur["budget"]) / abs(cur["budget"]) * 100, 2)
            trend = [
                {"period": pk, "value": series[pk]["value"], "budget": series[pk]["budget"]}
                for pk in sorted(series, key=sort_key) if not series[pk].get("unavailable")
            ]
        out.append({
            "metric": k["display_name"] or k["metric"], "category": k["category"],
            "period": period, "value": cur["value"] if cur else None, "unit": k["unit"], "currency": currency,
            "direction_good": k["direction_good"], "prior_value": None,
            "has_data": has_data, "deltas": deltas, "trend": None,
            "is_anomaly": False, "aggregated": True, "granularity": granularity,
            "aggregation_type": agg,
            "unavailable": bool(cur and cur.get("unavailable")),
            "report_id": None, "review_status": None, "assigned_email": None,
            "narrative": None, "sources": [],
            "confidence": None, "faithfulness": None,
            "chart_data": {"trend": trend} if (include_charts and trend) else None,
            "trend_streak": None,
        })
    # Movers first (largest absolute budget variance).
    out.sort(key=lambda f: abs(f["deltas"]["budget_var_abs"] or 0), reverse=True)
    return out


@app.get("/facts")
def list_facts(period: str, granularity: str = "month", include_charts: bool = True,
               user: CurrentUser = Depends(require_read)) -> list[dict]:
    """Dashboard cards for the ACTIVE dataset. Driven by the user's KPI
    selection (kpi_configs): shows exactly the selected KPIs, each LEFT JOINed
    to its computed fact for the period — so a selected KPI with no data for
    this period still renders an empty card (never silently hidden). If no KPIs
    were selected yet, falls back to every metric in the active dataset.

    granularity=quarter|year aggregates monthly values per each KPI's rule."""
    if granularity not in GRANULARITIES:
        raise HTTPException(status_code=422, detail=f"granularity must be one of {GRANULARITIES}")
    if granularity != "month":
        conn = get_connection()
        try:
            ds = active_dataset_id(conn)
            if ds is None:
                return []
            return _aggregated_facts(conn, ds, period, granularity, include_charts)
        finally:
            conn.close()
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return []

        currency = dataset_currency(conn, ds)
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

        sensitive = user.role == "admin"   # gate cost/latency/emails to admins
        # Batch the per-card report + feedback lookups into two queries instead of
        # ~3 per card (was an N+1 over the dashboard's hot path).
        metric_ids = [r["metric_id"] for r in rows if r["metric_id"] is not None]
        reports_by_metric, edited_by_report, reviewer_by_report = {}, {}, {}
        if metric_ids:
            ph = ",".join("?" * len(metric_ids))
            for rr in conn.execute(
                f"""SELECT gr.id, gr.metric_id, gr.narrative, gr.sources, gr.confidence,
                           gr.faithfulness, gr.cost_usd, gr.latency_ms, gr.user_email,
                           gr.review_status, gr.assigned_email
                    FROM generated_reports gr
                    WHERE gr.metric_id IN ({ph}) AND gr.period = ?
                      AND (gr.prompt_version IS NULL OR gr.prompt_version NOT LIKE '%-digest')
                    ORDER BY gr.id ASC""",
                (*metric_ids, period),
            ).fetchall():
                reports_by_metric[rr["metric_id"]] = rr   # ASC → latest id wins
            report_ids = [rr["id"] for rr in reports_by_metric.values()]
            if report_ids:
                ph2 = ",".join("?" * len(report_ids))
                for fr in conn.execute(
                    f"""SELECT report_id, action, edited_text, user_email
                        FROM feedback WHERE report_id IN ({ph2}) ORDER BY id ASC""",
                    tuple(report_ids),
                ).fetchall():
                    if fr["action"] == "edited" and fr["edited_text"] is not None:
                        edited_by_report[fr["report_id"]] = fr["edited_text"]
                    if fr["user_email"] is not None:
                        reviewer_by_report[fr["report_id"]] = fr["user_email"]

        card_data = _prefetch_cards(
            conn,
            {r["metric_id"]: r["metric"] for r in rows if has_usable_value(r["metric_id"], r["value"])},
            period,
        )
        out = []
        for r in rows:
            has_data = has_usable_value(r["metric_id"], r["value"])
            report = reports_by_metric.get(r["metric_id"]) if has_data else None
            edited = edited_by_report.get(report["id"]) if report else None
            reviewer = reviewer_by_report.get(report["id"]) if report else None
            out.append({
                "metric": r["metric"],
                "category": r["category"],
                "period": period,
                "value": r["value"],
                "unit": r["unit"], "currency": currency,
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
                "review_status": report["review_status"] if report else None,
                "assigned_email": report["assigned_email"] if report else None,
                "narrative": (edited or report["narrative"]) if report else None,
                "original_narrative": report["narrative"] if (report and edited) else None,
                "edited": edited is not None,
                "sources": json.loads(report["sources"]) if (report and report["sources"]) else [],
                "confidence": report["confidence"] if report else None,
                "faithfulness": report["faithfulness"] if report else None,
                # Cost, latency and user emails are operational/PII detail — only
                # admins see them (matches /costs being admin-only).
                "cost_usd": report["cost_usd"] if (report and sensitive) else None,
                "latency_ms": report["latency_ms"] if (report and sensitive) else None,
                "generated_by": report["user_email"] if (report and sensitive) else None,
                "reviewed_by": reviewer if (report and sensitive) else None,
                "chart_data": card_data[r["metric_id"]][0] if include_charts and has_data else None,
                "trend_streak": card_data[r["metric_id"]][1] if has_data else None,
            })
        return out
    finally:
        conn.close()


# ---------- Flexible ingestion (Addendum v1.1: detect -> map -> pick) ----------


@app.post("/compute")
def compute(_: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        facts = compute_and_store(conn, ds)
        if ds is not None:
            # Refresh derived KPIs off the new base facts so they don't go stale.
            if derived_metrics.materialize(conn, ds) > 0:
                facts = compute_and_store(conn, ds)
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
        return _bridge_from_rows(row, None)
    mv = conn.execute(
        """
        SELECT quantity, budget_quantity, price, budget_price
        FROM metric_values WHERE metric_id = ? AND period = ?
        """,
        (metric_id, period),
    ).fetchone()
    return _bridge_from_rows(None, mv)


def _bridge_from_rows(pvm_row, mv_row) -> dict | None:
    """Stored multi-item P/V/M bridge if there is one, else an on-the-fly
    single-row bridge from the period's qty/price detail."""
    if pvm_row is not None:
        return {"volume": pvm_row["volume"], "price": pvm_row["price"], "mix": pvm_row["mix"]}
    if mv_row:
        vb = bridge_for_metric_row(
            mv_row["quantity"], mv_row["budget_quantity"], mv_row["price"], mv_row["budget_price"]
        )
        if vb is not None:
            return {"volume": vb.volume, "price": vb.price, "mix": vb.mix}
    return None


def _fact_by_metric_id(conn, metric_id: int, period: str) -> ComputedFact | None:
    row = conn.execute(
        """
        SELECT m.name AS metric, m.category, m.unit, d.currency, cf.period, cf.value, cf.prior_value,
               cf.mom_pct, cf.yoy_pct, cf.budget_var_abs, cf.budget_var_pct, cf.trend, cf.is_anomaly
        FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
        LEFT JOIN datasets d ON d.id = m.dataset_id
        WHERE cf.metric_id = ? AND cf.period = ?
        """,
        (metric_id, period),
    ).fetchone()
    if row is None:
        return None
    return ComputedFact(
        metric=row["metric"], category=row["category"], period=row["period"],
        value=row["value"], unit=row["unit"], currency=row["currency"] or "USD", prior_value=row["prior_value"],
        deltas=Deltas(
            mom_pct=row["mom_pct"], yoy_pct=row["yoy_pct"],
            budget_var_abs=row["budget_var_abs"], budget_var_pct=row["budget_var_pct"],
        ),
        trend=row["trend"], is_anomaly=bool(row["is_anomaly"]),
        variance_bridge=_load_bridge(conn, row["metric"], period),
    )


def _load_computed_fact(conn, metric: str, period: str) -> ComputedFact:
    metric_id = _active_metric_id(conn, metric)
    fact = _fact_by_metric_id(conn, metric_id, period) if metric_id is not None else None
    if fact is None:
        raise HTTPException(
            status_code=404,
            detail=f"No computed facts for metric='{metric}' period='{period}'. Run /compute first.",
        )
    return fact


@app.get("/reports/{report_id}/grounding")
def report_grounding(report_id: int, _: CurrentUser = Depends(require_read)) -> dict:
    """Narrative Drill-Down (v3.0): per-sentence attribution to the exact computed
    facts and context docs that ground it. Deterministic, scoped to the caller's
    universe, computed on read (no LLM)."""
    conn = get_connection()
    try:
        row = conn.execute(
            f"""SELECT gr.narrative, gr.sources, gr.period, gr.metric_id
                FROM generated_reports gr JOIN metrics m ON m.id = gr.metric_id
                JOIN datasets d ON d.id = m.dataset_id
                WHERE gr.id = ? AND {_scope_pred('d')}""",
            (report_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
        fact = _fact_by_metric_id(conn, row["metric_id"], row["period"])
        if fact is None:
            raise HTTPException(status_code=404, detail="No computed fact for this report")
        # Rebuild the cited context snippets (bodies needed for causal attribution).
        try:
            cited = json.loads(row["sources"]) if row["sources"] else []
        except (ValueError, TypeError):
            cited = []
        ctx = []
        for s in cited:
            sid = str(s.get("id", ""))
            digits = sid.replace("ctx_", "")
            body = ""
            if digits.isdigit():
                b = conn.execute(
                    "SELECT body FROM context_documents WHERE id = ?", (int(digits),)
                ).fetchone()
                body = b["body"] if b else ""
            ctx.append(ContextSnippet(id=sid, type=s.get("type", "note"),
                                      title=s.get("title", ""), body=body))
        return attribute(row["narrative"], fact, ctx)
    finally:
        conn.close()


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
        audit.record(conn, "create", "context", added.id, actor_id=user.id,
                     actor_email=user.email, summary={"title": added.title})
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
def generate_insight_endpoint(req: GenerateInsightRequest, user: CurrentUser = Depends(require_member)) -> InsightOutput:
    _enforce_budget(user)
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

        # Sources are only ever retrieved from the caller's own Context Library.
        # Hand-supplied context (a local-dev/testing convenience) is ignored when
        # sign-in is on, so nobody can make a draft cite a note that doesn't exist.
        context = [] if auth_active() else req.context
        retrieval_scores: list[float] | None = None
        if not context and req.use_retrieval:
            # Retrieval (query embedding + vector search) is itself cached,
            # namespace-scoped: any context/data change invalidates it.
            # Key by dataset id so the demo and real universes never share a
            # retrieval entry (their datasets always have distinct ids).
            retr_key = f"retrieval:ds{ds}:{req.metric}:{req.period}:k{settings.retrieval_k}"
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
        # Include dataset id + demo scope so equal-valued facts in different
        # datasets (or the demo universe) never collide on one cached narrative.
        f"{PROMPT_VERSION}:{domain_slug}:ds{ds}:{'demo' if is_demo_scope() else 'real'}",
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
        rid = int(cur.lastrowid)
        audit.record(conn, "generate", "report", rid, actor_id=user.id, actor_email=user.email,
                     summary={"metric": insight.metric, "period": insight.period,
                              "faithfulness": insight.faithfulness})
        return rid
    finally:
        conn.close()


@app.post("/ask")
def ask_endpoint(payload: dict, user: CurrentUser = Depends(require_member)) -> dict:
    """Grounded Q&A about one metric (v2.9). Same guarantees as narratives:
    numbers only from computed facts, causes only from retrieved context, and
    an honest "the data doesn't answer that" when neither covers the question."""
    _enforce_budget(user)
    metric = (payload.get("metric") or "").strip()
    period = (payload.get("period") or "").strip()
    question = (payload.get("question") or "").strip()
    if not metric or not period or not question:
        raise HTTPException(status_code=422, detail="metric, period, and question are required")
    if len(question) > 500:
        raise HTTPException(status_code=422, detail="Question too long (max 500 chars)")
    # Conversation memory (Phase 3): prior [{question, answer}] turns in this
    # thread, so follow-ups resolve against the chat. Client-managed and capped.
    history = payload.get("history")
    history = history[-6:] if isinstance(history, list) else None

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
        qa = answer_question(get_llm_client(), fact, context, question, history)
    except LLMGenerationError as exc:
        raise HTTPException(status_code=503, detail=f"Q&A unavailable: {exc}") from exc

    context_by_id = {c.id: c for c in context}
    sources = [
        {"id": cid, "type": context_by_id[cid].type, "title": context_by_id[cid].title}
        for cid in qa.sources_used if cid in context_by_id
    ]
    _log_llm_call(
        "/ask",
        settings.openai_model if settings.llm_provider == "openai" else settings.anthropic_model,
        qa.prompt_tokens, qa.completion_tokens, qa.cost_usd, None, user.id,
    )
    return {"answer": qa.answer, "sources": sources, "grounded": qa.grounded}


def _items_for_channels(digest: DigestOutput) -> list[dict]:
    return [{
        "metric": it.metric, "period": digest.period, "value": it.headline,
        "delta": f"{it.budget_var_pct:+.1f}% vs plan" if it.budget_var_pct is not None else "",
        "narrative": it.detail,
    } for it in digest.items]


@app.get("/digest", response_model=DigestOutput)
def digest_preview(period: str, top_n: int = 5, _: CurrentUser = Depends(require_read)) -> DigestOutput:
    """The close's digest as leadership would receive it: approved commentary
    only, plus how many KPIs are still waiting and whether it was already sent.
    Deterministic and free (no AI call)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        digest = build_approved_digest(conn, period, dataset_id=ds, top_n=top_n)
        digest.sent_at = last_sent_at(conn, ds, period)
        return digest
    finally:
        conn.close()


@app.post("/digest/send", response_model=DigestOutput)
def digest_send(period: str, top_n: int = 5, user: CurrentUser = Depends(require_write)) -> DigestOutput:
    """Send the close's digest to the configured channels. Refused until every
    KPI in the close is approved, so leadership never receives unreviewed text."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        digest = build_approved_digest(conn, period, dataset_id=ds, top_n=top_n)
        if not digest.total_kpis:
            raise HTTPException(status_code=409, detail="This close has no KPIs to send.")
        if not digest.ready:
            n = len(digest.pending)
            names = ", ".join(digest.pending[:3]) + (f" and {n - 3} more" if n > 3 else "")
            raise HTTPException(
                status_code=409,
                detail=f"{n} KPI{'s' if n != 1 else ''} still need approval before the digest can go out: {names}.")
        record_digest_run(conn, ds, period, top_n, [it.model_dump() for it in digest.items], None, "sent")
        audit.record(conn, "send", "digest", period, actor_id=user.id, actor_email=user.email,
                     summary={"dataset_id": ds, "items": len(digest.items)})
        digest.sent_at = last_sent_at(conn, ds, period)
    finally:
        conn.close()
    _notify_async("digest_generated", period=period, items=_items_for_channels(digest))
    return digest


@app.get("/board-pack")
def board_pack(period: str, user: CurrentUser = Depends(require_read)) -> HTMLResponse:
    """A self-contained, print-ready HTML board pack for the active dataset and
    period — the same KPIs/deltas/narratives as the dashboard, assembled into one
    shareable document (save-as-PDF or email). Deterministic; no LLM call here."""
    period = (period or "").strip()
    if not period:
        raise HTTPException(status_code=422, detail="period is required")
    # Reuse the dashboard's exact facts (respects workspace/demo scope + charts).
    facts = list_facts(period=period, granularity="month", include_charts=True, user=user)
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        ds_name = None
        if ds is not None:
            row = conn.execute("SELECT name FROM datasets WHERE id = ?", (ds,)).fetchone()
            ds_name = row["name"] if row else None
    finally:
        conn.close()
    from app.board_pack import collect_facts
    conn = get_connection()
    try:
        approved = {f["metric"]: f["narrative"] for f in collect_facts(conn, ds, period)} if ds else {}
        for fact in facts:
            fact["narrative"] = approved.get(fact["metric"])
    finally:
        conn.close()
    doc = build_board_pack_html(facts, period, {"dataset_name": ds_name})
    return HTMLResponse(doc)


# ---------- Scheduling: jobs, digest history, and the cron tick (v3.1) ----------


@app.get("/schedules")
def list_schedules(_: CurrentUser = Depends(require_admin)) -> list[dict]:
    conn = get_connection()
    try:
        return scheduling.list_jobs(conn)
    finally:
        conn.close()


@app.post("/schedules", status_code=201)
def create_schedule(payload: dict, _: CurrentUser = Depends(require_admin)) -> dict:
    kind = (payload.get("kind") or "").strip()
    cadence = (payload.get("cadence") or "").strip()
    if kind not in scheduling.KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {scheduling.KINDS}")
    if cadence not in scheduling.CADENCES:
        raise HTTPException(status_code=422, detail=f"cadence must be one of {scheduling.CADENCES}")
    conn = get_connection()
    try:
        if kind == "digest":
            config = {"top_n": int(payload.get("top_n", 5))}
        elif kind == "board_pack":
            recipients = [e.strip() for e in (payload.get("recipients") or []) if e and e.strip()]
            config = {"recipients": recipients}
        else:
            config = {}
        job_id = scheduling.create_job(
            conn, kind, cadence,
            dataset_id=payload.get("dataset_id"),
            config=config,
            enabled=bool(payload.get("enabled", True)),
        )
        return {"id": job_id}
    finally:
        conn.close()


@app.patch("/schedules/{job_id}")
def update_schedule(job_id: int, payload: dict, _: CurrentUser = Depends(require_admin)) -> dict:
    conn = get_connection()
    try:
        scheduling.set_job_enabled(conn, job_id, bool(payload.get("enabled", True)))
        return {"id": job_id}
    finally:
        conn.close()


@app.delete("/schedules/{job_id}", status_code=204)
def remove_schedule(job_id: int, _: CurrentUser = Depends(require_admin)) -> None:
    conn = get_connection()
    try:
        if not scheduling.delete_job(conn, job_id):
            raise HTTPException(status_code=404, detail=f"Schedule {job_id} not found")
    finally:
        conn.close()


@app.get("/digests/history")
def digest_history(_: CurrentUser = Depends(require_read)) -> list[dict]:
    conn = get_connection()
    try:
        dataset_id = active_dataset_id(conn)
        return scheduling.list_digest_runs(conn, dataset_id) if dataset_id is not None else []
    finally:
        conn.close()


@app.post("/internal/scheduler/tick")
def scheduler_tick(request: Request) -> dict:
    """Cron entry point. Gated by the shared SCHEDULER_TOKEN (no user JWT), so an
    external pinger can drive scheduled digests + anomaly scans. Disabled (503)
    until SCHEDULER_TOKEN is configured."""
    if not settings.scheduler_token:
        raise HTTPException(status_code=503, detail="Scheduling is not configured")
    token = request.headers.get("x-scheduler-token", "")
    if not hmac.compare_digest(token, settings.scheduler_token):
        raise HTTPException(status_code=401, detail="Invalid scheduler token")
    try:
        llm_client = get_llm_client()
    except Exception:  # noqa: BLE001 - anomaly scans still run deterministically without an LLM
        llm_client = None
    conn = get_connection()
    try:
        return scheduling.run_due_jobs(conn, llm_client=llm_client)
    finally:
        conn.close()


@app.post("/feedback", status_code=201)
def feedback_endpoint(fb: FeedbackIn, user: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        # Scope the report to the caller's universe (demo vs real) so feedback can
        # never reach across it — the report's dataset must match the request scope.
        exists = conn.execute(
            f"""SELECT gr.narrative FROM generated_reports gr
                JOIN metrics m ON m.id = gr.metric_id
                JOIN datasets d ON d.id = m.dataset_id
                WHERE gr.id = ? AND {_scope_pred('d')}""",
            (fb.report_id,),
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Report {fb.report_id} not found")
        cur = conn.execute(
            """INSERT INTO feedback (report_id, action, edited_text, reason, user_id, user_email)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (fb.report_id, fb.action, fb.edited_text, fb.reason, user.id, user.email),
        )
        conn.commit()
        # Version history (v5.0): an edit snapshots a new version. The first edit
        # also captures the original (v1) so the diff chain starts from generation.
        if fb.action == "edited" and fb.edited_text:
            have = conn.execute(
                "SELECT COUNT(*) AS n FROM report_versions WHERE report_id = ?", (fb.report_id,)
            ).fetchone()["n"]
            if have == 0 and exists["narrative"]:
                review.add_version(conn, fb.report_id, exists["narrative"], None, "original")
            review.add_version(conn, fb.report_id, fb.edited_text, user.id, user.email)
        audit.record(conn, fb.action, "report", fb.report_id, actor_id=user.id,
                     actor_email=user.email, summary={"edited": bool(fb.edited_text)})
        return {"feedback_id": int(cur.lastrowid)}
    finally:
        conn.close()


# ---------- Collaborative review (v5.0) ----------


@app.get("/me")
def whoami(request: Request, user: CurrentUser = Depends(get_current_user)) -> dict:
    """The current user's identity, global role, and (if in a workspace) their
    per-workspace role."""
    return {"id": user.id, "email": user.email, "role": user.role,
            "workspace_role": getattr(request.state, "ws_role", None)}


# ---------- Workspaces (v4.0 multi-tenancy) ----------


@app.get("/workspaces")
def list_workspaces(request: Request, user: CurrentUser = Depends(get_current_user)) -> dict:
    """The caller's workspaces + which one is active for this request."""
    conn = get_connection()
    try:
        mine = workspaces.list_user_workspaces(conn, user.id)
        active = getattr(request.state, "workspace_id", None)
        return {"active_id": active, "workspaces": mine}
    finally:
        conn.close()


@app.get("/workspaces/{ws_id}/members")
def list_ws_members(ws_id: int, user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    conn = get_connection()
    try:
        if auth_active() and not workspaces.is_member(conn, ws_id, user.id):
            raise HTTPException(status_code=404, detail="Workspace not found")
        return workspaces.list_members(conn, ws_id)
    finally:
        conn.close()


# ---------- Live data connectors (v4.0) ----------


@app.get("/connectors")
def list_connectors_endpoint(_: CurrentUser = Depends(require_read)) -> list[dict]:
    conn = get_connection()
    try:
        return connectors.list_connectors(conn, current_workspace())
    finally:
        conn.close()


@app.post("/connectors", status_code=201)
def create_connector_endpoint(payload: dict, user: CurrentUser = Depends(require_write)) -> dict:
    kind = (payload.get("kind") or "").strip()
    name = (payload.get("name") or "").strip()
    if kind not in connectors.KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {connectors.KINDS}")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    conn = get_connection()
    try:
        try:
            cid = connectors.create_connector(
                conn, kind, name, payload.get("config") or {},
                payload.get("dataset_name") or name, current_workspace())
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        audit.record(conn, "create", "connector", cid, actor_id=user.id,
                     actor_email=user.email, summary={"kind": kind, "name": name})
        return {"id": cid}
    finally:
        conn.close()


@app.post("/connectors/{connector_id}/sync")
def sync_connector_endpoint(connector_id: int, user: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        try:
            result = connectors.sync_connector(conn, connector_id, current_workspace())
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        audit.record(conn, "sync", "connector", connector_id, actor_id=user.id,
                     actor_email=user.email, summary=result)
        return result
    finally:
        conn.close()


@app.delete("/connectors/{connector_id}", status_code=204)
def delete_connector_endpoint(connector_id: int, _: CurrentUser = Depends(require_write)) -> None:
    conn = get_connection()
    try:
        if not connectors.delete_connector(conn, connector_id, current_workspace()):
            raise HTTPException(status_code=404, detail=f"Connector {connector_id} not found")
    finally:
        conn.close()


@app.get("/audit")
def audit_list(_: CurrentUser = Depends(require_read)) -> list[dict]:
    """The active workspace's audit trail (recent first). Admin-relevant but
    readable by any member for transparency."""
    conn = get_connection()
    try:
        return audit.list_entries(conn, current_workspace())
    finally:
        conn.close()


@app.get("/audit/verify")
def audit_verify(_: CurrentUser = Depends(require_admin)) -> dict:
    """Recompute the hash chain to prove the trail hasn't been tampered with."""
    conn = get_connection()
    try:
        return audit.verify(conn, current_workspace())
    finally:
        conn.close()


# ---- Feature routers split out of this module (app/routers/*) ----
from app.routers import admin as _admin_router  # noqa: E402
from app.routers import close as _close_router  # noqa: E402
from app.routers import datasets as _datasets_router
from app.routers import ingest as _ingest_router
from app.routers import insights as _insights_router  # noqa: E402
from app.routers import notifications as _notif_router  # noqa: E402
from app.routers import review as _review_router  # noqa: E402
from app.routers import ui as _ui_router

app.include_router(_ui_router.router)
app.include_router(_datasets_router.router)
app.include_router(_ingest_router.router)
app.include_router(_admin_router.router)
app.include_router(_notif_router.router)
app.include_router(_review_router.router)
app.include_router(_close_router.router)
app.include_router(_insights_router.router)
