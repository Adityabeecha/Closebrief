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
3. Paste the 7 secrets above into the service's **Environment** tab.
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

`.github/workflows/ci.yml` gates every push/PR to `main`: **ruff → pytest →
eval recall@5 ≥ 90% → docker build**. Render auto-deploys `main` on merge
(`autoDeploy: true`); CI does not call Render — it only decides mergeability.

- `.github/workflows/nightly-eval.yml` runs the full eval **with** the LLM nightly;
  needs `OPENAI_API_KEY` as a **repository** Actions secret.
- `.github/dependabot.yml` opens weekly pip + actions update PRs.

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
