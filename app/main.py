import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import authenticate
from app.config import auth_active, check_production_auth, settings
from app.datasets import (
    set_dataset_choice,
    set_demo_scope,
    set_workspace_scope,
)
from app.db import get_connection, init_db
from app.demo import seed_demo
from app.routes_common import (
    _LATENCIES,
    _context_store,
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


# ---- Feature routers split out of this module (app/routers/*) ----
from app.routers import admin as _admin_router  # noqa: E402
from app.routers import close as _close_router  # noqa: E402
from app.routers import context as _context_router
from app.routers import datasets as _datasets_router
from app.routers import facts as _facts_router
from app.routers import generate as _generate_router
from app.routers import ingest as _ingest_router
from app.routers import insights as _insights_router  # noqa: E402
from app.routers import notifications as _notif_router  # noqa: E402
from app.routers import ops as _ops_router
from app.routers import review as _review_router  # noqa: E402
from app.routers import ui as _ui_router
from app.routers import workspaces as _workspaces_router

app.include_router(_ui_router.router)
app.include_router(_datasets_router.router)
app.include_router(_ingest_router.router)
app.include_router(_facts_router.router)
app.include_router(_generate_router.router)
app.include_router(_context_router.router)
app.include_router(_workspaces_router.router)
app.include_router(_ops_router.router)
app.include_router(_admin_router.router)
app.include_router(_notif_router.router)
app.include_router(_review_router.router)
app.include_router(_close_router.router)
app.include_router(_insights_router.router)
