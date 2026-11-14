# Deploying Closebrief

Closebrief runs as a **single Docker web service** (FastAPI serves both the API
and the single-file UI at `/`). Postgres, Redis, and error tracking are external
managed services. Everything below has a free tier except OpenAI (pay-per-use).

## Architecture

```
                 ┌─────────────────────────────┐
  Browser  ────► │  Render web service (Docker) │  ── app.main:app (FastAPI)
                 │  serves UI at / + JSON API   │      + vendored UI at /vendor/*
                 └───────────┬─────────────────┘
                             │
        ┌────────────────────┼─────────────────────┐
        ▼                    ▼                     ▼
  Supabase Postgres     Upstash Redis          OpenAI API
  (+ pgvector)          (response cache)       (LLM + embeddings)
        │
        ▼
     Sentry  ◄── backend (Python SDK) + frontend (browser SDK, same DSN)
```

## Accounts & where each secret comes from

Set these in the **Render dashboard** (they are `sync: false` in `render.yaml`,
so they are never committed):

| Secret | From | Notes |
|---|---|---|
| `OPENAI_API_KEY` | platform.openai.com → API keys | Set a hard spend limit before going public |
| `DATABASE_URL` | Supabase → Settings → Database → **Transaction pooler** | Port **6543** (the app derives the 5432 session URL for DDL) |
| `SUPABASE_URL` | Supabase → Settings → API | `https://xxxx.supabase.co` |
| `SUPABASE_ANON_KEY` | Supabase → Settings → API | Public anon key |
| `SUPABASE_JWT_SECRET` | Supabase → Settings → API → JWT | HS256 secret |
| `SESSION_JWT_SECRET` | Generate with the command below | Separate Google session signing key |
| `REDIS_URL` | Upstash → your Redis DB | `rediss://…` (TLS) |
| `SENTRY_DSN` | Sentry → project → Client keys (DSN) | Public by design; also enables **frontend** error tracking |

Non-secret config (`APP_ENV=prod`, `LLM_PROVIDER=openai`, `VECTOR_BACKEND=pgvector`,
`AUTH_ENABLED=true`, model names) is baked into `render.yaml` — don't re-enter it.

## First deploy (Render free tier)

1. **Supabase**: create a project. pgvector is enabled automatically by the app's
   migration (`CREATE EXTENSION IF NOT EXISTS vector` in
   `app/migrations/pg_schema.sql`), so no manual SQL is required.
2. **Render** → New → **Blueprint** → connect `Adityabeecha/Closebrief` → it reads
   `render.yaml` and provisions one `free` web service.
3. Paste the secrets above into the service's **Environment** tab.
4. Create. On first boot the app **auto-migrates** the Postgres schema
   (`auto_migrate=true` in `app/config.py`), because the free tier has no
   `preDeployCommand`. On the paid `starter` plan you can instead restore the
   `preDeployCommand` migration in `render.yaml`.
5. Verify: open `https://<app>.onrender.com/health` → `{"status":"ok",...}`, then
   `/` for the UI.

> **Free tier sleeps** after ~15 min idle (≈50s cold start). To avoid this, either
> switch `plan: free` → `plan: starter` in `render.yaml`, or add an external
> uptime pinger (e.g. UptimeRobot) hitting `/health` every few minutes.

## CI/CD

`.github/workflows/ci.yml` gates every push/PR to `main`: **pip-audit -> ruff -> pytest ->
eval recall@5 >= 90% -> docker build**. Render deploys `main` automatically once
those checks pass (`autoDeployTrigger: checksPass` in `render.yaml`); CI does not call Render.

- `.github/workflows/nightly-eval.yml` runs the full eval **with** the LLM nightly;
  needs `OPENAI_API_KEY` as a **repository** Actions secret.
- `.github/dependabot.yml` opens weekly pip + actions update PRs.

## Releasing

Merge to `main` -> CI goes green -> Render deploys it -> check `/health` returns a
successful response. Pending Postgres migrations run on that first start. If the
Render service was created before `autoDeployTrigger` was set, also choose
**Settings -> Build & Deploy -> Auto-Deploy -> After CI Checks Pass** once in the
dashboard (or **Manual Deploy** to release by hand).

## Monitoring

- **Errors**: Sentry captures unhandled backend exceptions (Python SDK, init in
  `app/main.py`) and frontend JS errors (browser SDK, DSN served to the UI via
  `/auth/config`). Both use the same `SENTRY_DSN`.
- **Cost & latency**: the in-app **Cost & Usage** page (and `GET /costs`) shows
  LLM spend by day/endpoint, request p50/p95, and cache hit rate.
- **Uptime**: Render polls `/health`; add UptimeRobot for external monitoring.

## Local development

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e ".[dev]"
copy .env.example .env                               # add OPENAI_API_KEY
uvicorn app.main:app --reload                        # http://127.0.0.1:8000
```

With no `DATABASE_URL`/`REDIS_URL` the app falls back to local SQLite + FAISS +
in-memory cache, and with no `SUPABASE_URL` auth is bypassed — so it runs fully
offline for development.

## Workspace notification channels

After deploying migration `025_notification_workspace.sql`, the owner should
re-create notification channels once while signed into the intended workspace.
Existing channels keep `workspace_id = NULL` and deliver only for the unscoped
local-development scope; they are never assigned to a visitor or owner implicitly.

## Session signing key

Set `SESSION_JWT_SECRET` in Render independently of `SUPABASE_JWT_SECRET`.
Generate it locally with:

```bash
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

Changing this key signs every Google user out once. The Supabase secret remains
a backwards-compatible fallback; production logs a warning once per process
when `SESSION_JWT_SECRET` is missing.

## Visitor sandbox retention

The scheduled `retention_purge` job deletes inactive visitor workspaces after
`SANDBOX_RETENTION_DAYS` (default 365); `0` disables workspace deletion. Ensure
that job is enabled and the scheduler tick is called regularly.

Activity is the latest `created_at` still stored in datasets, generated reports
(joined through their metrics/dataset), context documents, or LLM calls in the
workspace. Workspaces with none of those rows use workspace creation time.
The inactivity check runs before the existing telemetry retention cleanup.
A shorter telemetry policy can remove LLM-call activity before the sandbox
window; only records still present contribute to the activity clock.
Sign-in and browsing alone do not refresh this activity clock.

A workspace is preserved if any member is a global `app_roles` admin, if its
existing `retention_days` is explicitly non-positive (keep indefinitely), or if
it contains demo data. Callers of `purge_inactive_sandboxes` may also protect
member IDs with `keep_user_ids`. Unscoped demo data is untouched. Workspace data,
notification channels, connectors, staged uploads, dataset job history and
report versions are deleted; the immutable audit log and user roles remain.

## Schema changes

1. Never edit an existing Postgres migration; add the next numbered file in app/migrations/pg/.
2. Every new column must be added by that Postgres migration and declared in app/db.py's _SQLITE_SCHEMA.
3. Also add a _SQLITE_MIGRATIONS entry so existing SQLite dev databases receive the same column.
4. Keep defaults, nullability, keys and type families equivalent; use a SQLite rebuild migration when ALTER cannot express the change.
5. Run tests/test_schema_parity.py locally and with TEST_DATABASE_URL in CI; backend drift or missing SQLite declarations/upgrades fails the test.
