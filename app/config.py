import os

from pydantic_settings import BaseSettings, SettingsConfigDict

# Environment profile: dev (default) | staging | prod.
# Selected via APP_ENV; `.env.<profile>` overrides `.env` when present.
APP_ENV = os.getenv("APP_ENV", "dev")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", f".env.{APP_ENV}"), extra="ignore"
    )

    environment: str = APP_ENV

    # Which provider the LLMClient / embedding layer use. "openai" or "anthropic".
    llm_provider: str = "openai"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"

    # Anthropic (kept so the provider stays swappable — PRD Section 6.1/8)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    retrieval_k: int = 5
    anomaly_z_threshold: float = 2.0

    # Embedding provider: "auto" (OpenAI if key set, else offline),
    # "local" (sentence-transformers), "openai", or "offline" (hashing).
    embedding_provider: str = "auto"

    # --- Storage (v1.1) ---
    # SQLite path (local dev fallback, used when database_url is empty)
    db_path: str = "./data/closebrief.db"
    # Supabase Postgres. database_url should be the TRANSACTION pooler
    # (port 6543) for runtime; migrations derive the session pooler (5432).
    database_url: str = ""
    database_url_direct: str = ""
    # Auto-create the Postgres schema on startup if it is missing. Lets the
    # Render free tier (no preDeployCommand) self-migrate on first boot. The
    # migration is idempotent; disable only if you manage schema out-of-band.
    auto_migrate: bool = True
    # v4.0 tenancy backstop: when true, each Postgres connection sets an
    # app.workspace_id GUC so RLS policies enforce workspace isolation at the DB
    # layer (defense in depth on top of app-level scoping). Off by default —
    # enable only after verifying on a Postgres instance. The RLS policies are
    # fail-open (allow-all when the GUC is unset), so migrations/background jobs
    # are unaffected either way.
    rls_enabled: bool = False

    # Vector backend: "auto" (pgvector when database_url set, else FAISS),
    # "faiss", or "pgvector".
    vector_backend: str = "auto"

    # --- Cache (v1.1) ---
    # Upstash/hosted Redis URL (rediss://...). Empty -> in-process cache.
    redis_url: str = ""
    cache_enabled: bool = True
    cache_ttl_seconds: int = 86400  # 24h

    # --- Auth (v1.2) ---
    # Supabase Auth project. When supabase_url is empty, auth is bypassed and
    # every request is treated as an anonymous admin (zero-config local dev).
    supabase_url: str = ""            # e.g. https://xxxx.supabase.co
    supabase_anon_key: str = ""       # public anon key (handed to the frontend)
    supabase_jwt_secret: str = ""     # HS256 shared secret (Settings -> API -> JWT)
    auth_enabled: bool = True         # False -> bypass auth even if supabase_url set

    # --- Google Sign-In (v5.6, optional) ---
    # OAuth client id. Empty = Google sign-in disabled entirely (the frontend
    # never even loads Google's script). Safe to expose — it's built for the page.
    google_client_id: str = ""
    # Role a brand-new Google user gets. COST WARNING: anyone with a Google
    # account can sign in, and LLM endpoints spend money — default to the lowest
    # role ("viewer") so a new self-service user can read but not spend.
    google_default_role: str = "viewer"
    # Comma-separated allowlist of emails that are (re)granted admin on Google
    # sign-in. Compared case-insensitively.
    google_admin_emails: str = ""
    # Secret used to sign Closebrief's own session tokens (issued to Google
    # users, who have no Supabase session). Falls back to the Supabase HS256
    # secret so a Supabase deploy needs no new secret; empty = Google login off.
    session_jwt_secret: str = ""
    # Let unauthenticated visitors use the app read-only (lowest role). Keeps the
    # app usable without signing in. Independent of demo_mode's sample data.
    allow_guest: bool = False

    # --- Demo mode (v2.9) ---
    # True -> seed a sample FP&A dataset at startup and let unauthenticated
    # visitors browse read-only (role "executive"). For portfolio/demo deploys.
    demo_mode: bool = False

    # --- Monitoring (v2.0) ---
    sentry_dsn: str = ""              # empty -> Sentry disabled

    # Public base URL of the app, used to build drill-in links in Slack/email
    # notifications (e.g. https://closebrief.onrender.com). No link when empty.
    app_base_url: str = "https://closebrief.onrender.com"

    # --- Notifications (v2.1) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    # Default to Resend's pre-verified sandbox sender so email works with zero
    # domain setup (sandbox can only deliver to your own Resend account email).
    # Once you verify a domain in Resend, set SMTP_FROM=Closebrief <alerts@yourdomain.com>.
    smtp_from: str = "Closebrief <onboarding@resend.dev>"
    smtp_starttls: bool = True
    # Resend HTTP API (recommended on cloud hosts that block outbound SMTP).
    # When set, EmailChannel sends via https://api.resend.com instead of SMTP.
    resend_api_key: str = ""
    webhook_secret: str = ""          # HMAC-SHA256 signing key for outbound webhooks
    # Shared secret an external cron sends (X-Scheduler-Token) to POST
    # /internal/scheduler/tick. Empty = scheduling disabled (endpoint returns 503).
    scheduler_token: str = ""


settings = Settings()


def session_secret() -> str:
    """Key for signing Closebrief's own session tokens (issued to Google users).
    Falls back to the Supabase HS256 secret so an existing deploy needs no new
    config. Empty -> we cannot mint or verify sessions, so Google login is off."""
    return settings.session_jwt_secret or settings.supabase_jwt_secret


def google_enabled() -> bool:
    """Google sign-in requires a client id (to verify `aud`) AND a session
    secret (to issue our own token). Missing either -> the feature is off and the
    frontend never contacts Google at all."""
    return bool(settings.google_client_id) and bool(session_secret())


def auth_active() -> bool:
    """Auth is enforced when it's enabled AND at least one identity provider is
    configured. Absent both (tests, local dev), it stays bypassed.

    Google counts as a provider: a Google-only deploy must still enforce auth,
    otherwise every request would fall through to the anonymous-admin bypass."""
    return settings.auth_enabled and (bool(settings.supabase_url) or google_enabled())


def resolved_vector_backend() -> str:
    if settings.vector_backend in ("faiss", "pgvector"):
        return settings.vector_backend
    return "pgvector" if settings.database_url else "faiss"


def session_pool_url() -> str:
    """Session-mode pooler URL for DDL/migrations: same Supabase pooler host
    as the transaction pooler, port 5432. (The db.*.supabase.co direct host
    is IPv6-only and unreachable from IPv4-only networks.)"""
    return settings.database_url.replace(":6543/", ":5432/")
