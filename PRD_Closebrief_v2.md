# PRD v2: Closebrief — Productize, Harden, Ship

**Version:** 2.0 (post-MVP scope)
**Owner:** Aditya
**Status:** Ready for build — **after** `../PRD_Narrative_BI_Addendum_v1.1.md`
**Predecessor:** `../PRD_Narrative_BI.md` (v1) + Addendum v1.1 (flexible ingestion, KPI selection, charts, P/V/M — ✅ implemented 2026-07-04)
**Primary reader:** Claude Code (executable spec — build top to bottom, milestone by milestone)

> **Precedence note:** where this document and Addendum v1.1 conflict, the addendum wins.
> In particular: **auth is out of the MVP** (Epic H is deferred to the very end of Milestone 5
> and only as a backlog item), and ingestion is the flexible detect→map→pick pipeline, not
> fixed columns.

---

## 0. Where we are (do not rebuild any of this)

Shipped and verified as of 2026-07-04:

| Area | State |
|---|---|
| Deterministic KPI engine (pandas) | ✅ value, MoM, YoY, budget variance, 12-mo trend, z-score anomaly |
| Faithfulness guard | ✅ number extraction ($4.2M / 370,000 / 12%), percent-vs-magnitude buckets, context-sourced numbers allowed, regenerate-once-then-flag |
| RAG core | ✅ Context Library CRUD, OpenAI embeddings behind `Embedder` interface, FAISS behind `VectorStore` interface (disk-persisted), top-k retrieval with metric-tag + effective-date filters |
| API | ✅ `/ingest /compute /context /generate-insight /digest /feedback /periods /facts` + OpenAPI docs |
| Persistence | ✅ SQLite; every report persisted with sources for audit |
| Eval harness | ✅ `python -m eval.run` — 15 golden cases: Recall@5 100%, Faithfulness 100%, Groundedness 100% |
| Web UI | ✅ single-file app at `/` (dashboard cards, digest, context library, CSV import) matching `ui/mockup/` design |
| Tests | ✅ 27 passing (KPI math, guard, retrieval) |

**Carry-over rules that still govern everything:**
1. The LLM never computes numbers (enforced in `app/generation/guard.py` + tests).
2. Vector store, embedder, and LLM stay behind interfaces — no vendor names in business logic.
3. LLM failure degrades to facts-only output, never a bare 500.

---

## 1. Problem statement for v2

The engine works but it is a **local demo**: one workspace, no login, SQLite on disk, no cost controls, silent token spend, and a UI that trusts a single user. To be portfolio-headline material ("deployed product with an eval story"), Closebrief needs: hosted storage, auth, caching + cost visibility, hardening, and a deployment URL — without breaking the eval numbers.

---

## 2. Goals & Non-Goals

### Goals
- One-command deploy; a public demo URL with login.
- Postgres + pgvector replacing SQLite + FAISS **via the existing interfaces** (prove the one-class-swap claim).
- Zero-surprise LLM spend: response caching, per-report token/cost logging, a visible cost estimate in the UI.
- Keep eval green: the harness must run against both storage backends and stay at targets.
- Redaction hook before prompt assembly (PRD v1 Section 8 debt).

### Non-Goals (unchanged from v1)
- No forecasting, no real-time streaming, no multi-tenant billing.
- No new BI charting engine. The UI stays thin.
- Growth/marketing use case, anomaly alerts, scheduled digests: **design for, still do not build** (Milestone 6 placeholder only).

---

## 3. User Stories

### Epic G — Hosted storage (Supabase)

**US-G1 — Postgres storage backend (5 pts)**
As an operator, I want the app on hosted Postgres, so that data survives redeploys and supports auth.
- Given `DATABASE_URL` is set, When the app starts, Then all tables live in Postgres (Supabase) and SQLite is untouched; absent the var, SQLite keeps working exactly as today.
- Must be a storage-layer change only (`app/db.py` + a thin repository seam if needed); no route signatures change.
- Alembic (or equivalent SQL migration files) for schema; `python -m app.db migrate` bootstraps.

**US-G2 — pgvector vector store (5 pts)**
As the system, I need embeddings in pgvector, so that vectors and rows share one database.
- Given `VECTOR_BACKEND=pgvector`, When context is added/edited/deleted, Then vectors upsert/delete in a `context_vectors` pgvector column; retrieval quality parity proven by `python -m eval.run --no-llm` ≥ same recall as FAISS.
- Implemented as a second `VectorStore` class; FAISS remains the default for local dev.
- Acceptance: switching backends is config-only — zero changes outside `app/context/vector_store.py` + config.

### Epic H — Auth & workspace

**US-H1 — Login (3 pts)**
As a workspace member, I want to sign in, so that financial data isn't public.
- Supabase Auth (email magic-link or password). The UI login screen already exists in the mockup — implement it for real.
- All API routes except `/health` require a valid JWT when `AUTH_ENABLED=true`; local dev default is off.

**US-H2 — Single-workspace guardrail (2 pts)**
- v2 stays single-workspace, but every table gains a `workspace_id` column (default `'default'`) so multi-tenancy later is a data migration, not a schema rewrite.

### Epic I — Cost, caching, latency

**US-I1 — Token & cost logging (3 pts)**
As an operator, I want per-report token counts and cost, so that spend is visible.
- Every LLM call records prompt/completion tokens + model + computed USD cost into a `llm_calls` table linked to the report.
- `GET /costs?period=` returns totals by day and by endpoint; the UI shows a small "≈ $0.0042" line on each card after generation.

**US-I2 — Response cache (3 pts)**
As the system, I want identical generate requests served from cache, so that regenerating a dashboard costs nothing.
- Cache key: (metric, period, computed-facts hash, context-ids+versions hash, prompt version, model).
- Backend: a `response_cache` DB table (no Redis dependency for v2; keep a `Cache` interface so Redis can slot in).
- `POST /generate-insight` returns cached result with `"cached": true` unless `force=true` is passed; UI "Regenerate" sends `force=true`.

**US-I3 — Latency budget (2 pts)**
- p95 single-insight generation < 5s (v1 NFR). Add timing middleware; expose p50/p95 in `/costs`. If over budget, first lever is prompt slimming, not model downgrade.

### Epic J — Privacy & hardening

**US-J1 — Redaction hook (3 pts)**
As a compliance-minded operator, I want a redaction pass before prompt assembly, so that sensitive strings never reach the LLM.
- A `redact(text) -> text` hook applied to context bodies and any free-text fields before they enter a prompt; default implementation masks emails, and a configurable denylist of terms (e.g. customer names) from a `redaction_terms` table.
- Document exactly what is sent to the LLM in README (v1 Section 8 debt).

**US-J2 — Input hardening (2 pts)**
- CSV ingest: cap file size (10 MB), cap rows (100k), reject formula-prefixed cells (`=`, `+@`) to block CSV injection on re-export.
- Context bodies capped at 10k chars; API rate limit (simple per-IP token bucket) when `AUTH_ENABLED=false`.

### Epic K — Deploy & story

**US-K1 — Deployment (3 pts)**
- Dockerfile + `docker compose up` for local; deploy API+UI to Render or Railway (single service — the UI is static and served by FastAPI already). Supabase hosts DB/auth.
- Health check endpoint wired to the platform; `.env.example` documents every var.

**US-K2 — README case study (2 pts)**
- README gains: architecture diagram, the eval table (before/after backend swap), a cost-per-month estimate for a 10-metric workspace, and the "why deterministic compute + RAG" design-decision narrative. This is the portfolio artifact.

### Epic L — Eval expansion (protects everything above)

**US-L1 — Eval on both backends (2 pts)**
- `python -m eval.run --backend faiss|pgvector` runs the same golden set against either vector store; CI-style script `scripts/check.sh` runs tests + retrieval-only eval and fails non-zero on regression.

**US-L2 — Golden set growth (2 pts)**
- Grow to 25 cases: add multi-metric-tag docs, conflicting context (two docs citing different figures — narrative must prefer the most recent, mirroring the mockup's conflict banner), and a redaction case (masked term must not appear in narrative).

---

## 4. Milestones (BUILD IN THIS ORDER)

### Milestone 4 — Cost & caching (pure local, lowest risk)
US-I1, US-I2, US-I3, US-J2.
**Exit:** regenerating an already-generated dashboard is free and instant; `/costs` reports spend; tests + eval still green.

### Milestone 5 — Supabase swap
US-G1, US-G2, US-L1, then US-H1, US-H2.
**Exit:** app runs on Postgres+pgvector with login; `eval.run --backend pgvector` meets the same targets; FAISS/SQLite still work for local dev.

### Milestone 6 — Privacy, deploy, story
US-J1, US-K1, US-K2, US-L2.
**Exit:** public URL with login; README case study with eval + cost tables; 25-case golden set green.

### Future (design-compatible, still not now)
Growth & marketing funnel narratives, anomaly alert pushes, scheduled digests (the cron surface), metric Q&A chat, true multi-workspace.

---

## 5. Success metrics

| Metric | Target |
|---|---|
| Faithfulness / Groundedness / Recall@5 | 100% / ≥90% / ≥90% on both backends |
| Cache hit rate on dashboard re-render | ≥ 95% |
| p95 insight latency (uncached) | < 5s |
| Cost per full 10-metric period generation | logged, visible, < $0.10 |
| Deploy | public URL, login required, one-command redeploy |

## 6. Risks

| Risk | Mitigation |
|---|---|
| pgvector swap breaks retrieval quality | eval harness runs per-backend before merge (US-L1) |
| Cache staleness after context edits | context-ids+versions in the cache key (US-I2) |
| Auth locks out local dev | `AUTH_ENABLED=false` default locally |
| Token costs creep | `/costs` visibility + cached-by-default regeneration |

---

*End of PRD v2.*
