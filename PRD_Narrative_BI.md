# PRD: Narrative BI — RAG-based AI Agent for Automated BI Reporting

**Version:** 1.0 (MVP → v1 scope)
**Owner:** Aditya
**Status:** Ready for build
**Primary reader:** Claude Code (this document is written to be executable — build from it top to bottom)

---

## 0. How to use this document (for Claude Code)

This PRD is the build spec. Work in the order defined in **Section 12 (Milestones)**. Do **not** build everything at once. Ship Milestone 1 (MVP) fully and get it running before starting Milestone 2.

**Non-negotiable architectural rule that governs the whole system:**
> The LLM never computes numbers. All KPI values, deltas, and trends are computed deterministically in Python/pandas. The LLM only receives pre-computed facts + retrieved context and writes narrative prose. If a number appears in any generated narrative, it must be traceable to a computed value passed into the prompt.

Violating this rule is the single biggest failure mode. Enforce it in code (Section 6.4) and in tests (Section 9).

---

## 1. Overview & Problem Statement

BI dashboards display numbers but not the *interpretation* of those numbers. Today, a financial analyst manually writes "variance commentary" every month — the plain-English explanation of why each KPI moved versus the prior period and versus budget. This is slow, repetitive, and inconsistent across analysts.

**Narrative BI** is an AI agent that ingests computed financial KPIs plus curated business context, and automatically generates grounded, executive-ready variance commentary as structured output that a dashboard, report, or digest can render.

**The product is the engine that turns computed facts + context into trustworthy narrative.** Everything else (dashboards, digests, alerts) is a rendering of that engine's structured output.

### 1.1 Anchor use case (build this first)
**FP&A monthly variance commentary.** Given a month's actuals for a set of financial KPIs, produce the commentary an FP&A analyst would otherwise write by hand.

### 1.2 Future use cases (design for, do not build yet)
Growth/marketing funnel explanations, anomaly alerts, scheduled digests, metric Q&A. The data model and API must not preclude these, but they are out of scope for v1.

---

## 2. Goals & Non-Goals

### 2.1 Goals
- Automate first-draft variance commentary for a defined set of FP&A KPIs.
- Guarantee numerical faithfulness: zero invented numbers in narratives.
- Ground every narrative in retrievable context (definitions, history, event notes).
- Output structured JSON consumable by any downstream surface.
- Provide an analyst feedback loop (accept / edit / reject).

### 2.2 Non-Goals
- Not building a new BI dashboard or charting engine (we render a thin demo UI only).
- Not doing forecasting or predictive modeling.
- Not multi-tenant SaaS with billing (single-workspace assumption for v1).
- Not real-time streaming data (batch / on-demand only).
- Not replacing the analyst — this produces a first draft for human review.

---

## 3. Personas

| Persona | Role in system | Primary need |
|---------|----------------|--------------|
| **FP&A Analyst** | Curates context, reviews & edits narratives | Save hours; trust the output; correct it easily |
| **Finance Executive** | Reads the digest | Plain-English "why," no SQL, no jargon |
| **Downstream dashboard** (consumer) | Renders structured JSON | Stable, well-typed output contract |

---

## 4. User Stories & Acceptance Criteria

Stories are grouped by epic. Format follows INVEST + Given-When-Then. Points on Fibonacci scale.

### Epic A — Data ingestion & KPI computation

**US-A1 — Ingest financial dataset (3 pts)**
As an analyst, I want to upload a CSV of monthly financials, so that the system has data to analyze.
- Given a CSV with columns `period, metric, value, budget`, When I POST it to `/ingest`, Then rows are validated and stored, and a summary of ingested metrics/periods is returned.
- Should reject files missing required columns with a clear 422 error naming the missing column.
- Should handle at least 24 months × 10 metrics without error.

**US-A2 — Compute KPIs deterministically (5 pts)**
As the system, I need to compute value, MoM delta, YoY delta, and budget variance for each metric, so that narratives are grounded in correct math.
- Given ingested data, When computation runs, Then for each metric+period it produces: current value, prior-period value, MoM % change, budget variance (absolute + %), and a 12-month trend direction.
- Must compute all figures in pandas, never via the LLM.
- Should flag a metric as "anomalous" when the MoM change exceeds a configurable z-score threshold vs its trailing history.
- Given a metric with no prior period, Then delta fields return null (not an error).

### Epic B — Context knowledge base & retrieval (the RAG core)

**US-B1 — Author context documents (3 pts)**
As an analyst, I want to add context documents (metric definitions, glossary, event notes), so that the AI can ground its explanations.
- Given the Context Library, When I add a document with `type`, `title`, `body`, and optional `metric_tags` and `effective_date`, Then it is stored and embedded.
- Context types supported: `definition`, `glossary`, `event_note`, `historical_commentary`, `policy`.

**US-B2 — Embed & index context (5 pts)**
As the system, I need to embed context documents into a vector index, so that relevant context can be retrieved semantically.
- Given a stored context document, When embedding runs, Then its vector is added to the FAISS index (MVP) with metadata linking back to the source document.
- Must persist the index to disk so it survives restart.
- Should re-embed on document edit and remove the vector on document delete.

**US-B3 — Retrieve relevant context (5 pts)**
As the system, I need to retrieve the top-k most relevant context chunks for a given metric + period, so that the LLM has grounding without being flooded.
- Given a metric+period and its computed facts, When retrieval runs, Then it returns top-k context chunks (k configurable, default 5) ranked by semantic similarity, filtered by `metric_tags` where present and by `effective_date <= period`.
- Must return the source id + type for each chunk (for citation).
- Should never return more than k chunks (token/cost control).

### Epic C — Narrative generation (grounded LLM)

**US-C1 — Generate metric narrative (8 pts)**
As an analyst, I want an AI-generated explanation for each KPI movement, so that I don't write it by hand.
- Given computed facts + retrieved context for a metric, When generation runs, Then a 2–4 sentence variance narrative is produced that references the actual computed numbers and the retrieved context.
- **Must not** contain any numeric figure that was not present in the computed facts passed to the prompt (verified by the numeric-faithfulness check, Section 9.2).
- Must return structured output: `metric`, `period`, `value`, `deltas`, `narrative`, `sources[]` (context ids used), `confidence` (High/Medium/Low).
- Confidence is Low when fewer than 2 relevant context chunks were retrieved; otherwise Medium/High per retrieval score.
- Given the LLM API fails, Then the endpoint returns a 503 with a retriable error, and the computed facts are still returned so the card can render without narrative.

**US-C2 — Generate executive digest (5 pts)**
As an executive, I want a top-movements summary, so that I understand the month without reading every card.
- Given all metric narratives for a period, When digest generation runs, Then it returns the top N movements (default 5) ranked by absolute budget variance, each as a one-line headline + one explanatory sentence.

### Epic D — API & output contract

**US-D1 — Expose REST API (5 pts)**
As a downstream consumer, I need well-typed endpoints, so that I can integrate the engine.
- Endpoints: `POST /ingest`, `POST /compute`, `POST /context`, `GET /context`, `POST /generate-insight`, `POST /digest`, `POST /feedback`.
- Must auto-generate OpenAPI docs (FastAPI).
- All responses conform to Pydantic schemas defined in Section 7.

### Epic E — Feedback loop

**US-E1 — Capture analyst feedback (3 pts)**
As an analyst, I want to accept, edit, or reject a narrative, so that the system logs quality and I can correct errors.
- Given a generated narrative, When I POST feedback (`accepted` | `edited` + edited_text | `rejected` + reason), Then it is persisted linked to the narrative id.
- Feedback data must be queryable for later evaluation.

### Epic F — Demo UI (thin)

**US-F1 — Insights dashboard (5 pts)**
As an analyst, I want to see narrative cards for a period, so that I can review output visually.
- Given a computed period, When I open the dashboard, Then I see one card per metric with value, deltas (color-coded), narrative, source chips, confidence badge, and thumbs up/down + edit controls.

**US-F2 — Context library & digest views (3 pts)**
As an analyst, I want to manage context and read the digest, so that the demo shows the full loop.

---

## 5. System Architecture

```
                 ┌─────────────────────────────────────────────┐
   CSV / data ──►│  Ingestion (validate, store)                │
                 └───────────────┬─────────────────────────────┘
                                 ▼
                 ┌─────────────────────────────────────────────┐
                 │  KPI Compute (pandas) — DETERMINISTIC        │
                 │  value, MoM, YoY, budget variance, anomaly   │
                 └───────────────┬─────────────────────────────┘
                                 │ computed facts
   context docs ──► Embed ──► FAISS index ──► Retrieve top-k ────┤
                                 ▼                               ▼
                 ┌─────────────────────────────────────────────┐
                 │  Narrative Generator (LLM)                  │
                 │  input: computed facts + retrieved context  │
                 │  output: narrative prose ONLY               │
                 └───────────────┬─────────────────────────────┘
                                 ▼
                 ┌─────────────────────────────────────────────┐
                 │  Numeric-faithfulness guard  (reject/flag)  │
                 └───────────────┬─────────────────────────────┘
                                 ▼
                 Structured JSON ──► FastAPI ──► Demo UI / dashboard / digest
```

### 5.1 Layer responsibilities (strict separation)
- **Deterministic layer** owns all math. Pure functions, unit-tested.
- **Retrieval layer** owns context only. Never sees or returns numbers.
- **Generation layer** owns prose only. Receives facts as read-only input; forbidden to introduce new figures.
- **Guard layer** validates generation output before it leaves the system.

---

## 6. Technical Stack & Decisions

### 6.1 MVP stack (Milestone 1)
| Concern | Choice | Rationale |
|---------|--------|-----------|
| Language | Python 3.11+ | Analytics + AI ecosystem |
| API | **FastAPI** + Uvicorn | Async, Pydantic validation, auto OpenAPI docs |
| Data compute | pandas | Deterministic KPI math |
| Vector store | **FAISS** (local, persisted to disk) | Simple, fast, no external service |
| Embeddings | `sentence-transformers` (local) OR provider embeddings | Local avoids cost; provider is higher quality — make it swappable behind an interface |
| LLM | Provider API behind an `LLMClient` interface | Swappable; do not hardcode a vendor in business logic |
| Storage (MVP) | SQLite + local files | Zero setup; good enough to prove the loop |
| Config | `.env` + pydantic-settings | Keys, model names, k, thresholds |

### 6.2 v1 stack (Milestone 3)
| Concern | Change | Rationale |
|---------|--------|-----------|
| Storage | **Supabase (Postgres)** | Hosted Postgres + auth + storage |
| Vector store | **pgvector** (replaces FAISS) | One database instead of two moving parts |
| Cache | Redis (optional) | Avoid re-paying LLM cost for identical requests |
| Auth | Supabase Auth | Real login for the demo |

> Keep the vector store behind a `VectorStore` interface so swapping FAISS → pgvector is a one-class change, not a rewrite.

### 6.3 Frontend
- **MVP:** Streamlit (fast, believable, hours not days).
- **v1 (optional stretch):** Next.js + Tailwind hitting the FastAPI backend (leans the portfolio toward dev; matches the Claude Design mockups).

### 6.4 The faithfulness guard (implement as real code)
A function that, given the computed facts and the generated narrative, extracts every number from the narrative and asserts each maps (within rounding tolerance) to a value in the facts. On failure: either regenerate once with a stricter prompt, or return the narrative flagged `faithfulness: failed` and downgrade confidence. Never silently pass an unverified number.

---

## 7. Data Model & Output Contract

### 7.1 Core tables (SQLite MVP → Postgres v1)
```
metrics            (id, name, category, unit, direction_good)      -- direction_good: 'up' or 'down'
metric_values      (id, metric_id, period, value, budget)
computed_facts     (id, metric_id, period, value, prior_value,
                    mom_pct, yoy_pct, budget_var_abs, budget_var_pct,
                    trend, is_anomaly)
context_documents  (id, type, title, body, metric_tags[],
                    effective_date, created_at)
context_vectors    (id, context_id, embedding)                    -- FAISS metadata / pgvector column
generated_reports  (id, metric_id, period, narrative, sources[],
                    confidence, faithfulness, created_at)
feedback           (id, report_id, action, edited_text, reason, created_at)
```

### 7.2 Insight output schema (the public contract)
```json
{
  "metric": "Net Revenue",
  "category": "Revenue",
  "period": "2025-03",
  "value": 4200000,
  "unit": "USD",
  "deltas": {
    "mom_pct": -12.0,
    "yoy_pct": 4.5,
    "budget_var_abs": -370000,
    "budget_var_pct": -8.1
  },
  "is_anomaly": true,
  "narrative": "Net revenue fell 12% month over month to $4.2M, 8% below plan, driven mainly by enterprise churn following the March price change; new-logo bookings partially offset the decline.",
  "sources": [
    {"id": "ctx_014", "type": "event_note", "title": "March 2025 pricing change"},
    {"id": "ctx_003", "type": "historical_commentary", "title": "Q1 seasonality"}
  ],
  "confidence": "High",
  "faithfulness": "passed"
}
```

This schema is the stable interface. UI, digest, and future consumers all build on it.

---

## 8. Non-Functional Requirements

| Category | Requirement |
|----------|-------------|
| **Faithfulness** | 0 invented numbers in accepted narratives (hard requirement, enforced by guard + tests) |
| **Latency** | Single insight generation < 5s p95; cache identical requests |
| **Cost** | Log tokens per report; retrieval capped at k chunks; expose a cost estimate |
| **Auditability** | Every report logs which context ids and which computed facts fed it |
| **Security/Privacy** | Financial data is sensitive: document exactly what is sent to the LLM; support a redaction/anonymization hook before prompt assembly; keep API keys server-side only |
| **Reliability** | LLM failure degrades gracefully to facts-only cards, never a 500 with no data |
| **Portability** | Vector store and LLM behind interfaces; swappable without touching business logic |

---

## 9. Evaluation (the differentiator — build this, do not skip)

A small but real eval harness. This is what separates the project from a demo.

### 9.1 Golden set
Create ~15–20 hand-authored test cases: computed facts + context + an ideal narrative (or ideal key-points). Store as fixtures.

### 9.2 Numeric faithfulness metric
Automated: parse numbers from generated narrative, verify each maps to a computed fact within tolerance. Report **faithfulness rate** across the golden set. Target: 100%.

### 9.3 Groundedness metric
Check that claimed causes in the narrative correspond to a retrieved context chunk (LLM-as-judge or keyword overlap). Report **groundedness rate**.

### 9.4 Retrieval quality
For each golden case, verify the expected context doc appears in top-k. Report **recall@k**.

### 9.5 Acceptance proxy
Track, from the feedback table, the **edit-free acceptance rate** over time.

Expose an eval report (`python -m eval.run`) that prints these metrics. This is a headline artifact for the portfolio write-up.

---

## 10. Repository Structure (build to this layout)

```
narrative-bi/
├── README.md
├── PRD_Narrative_BI.md
├── .env.example
├── pyproject.toml
├── app/
│   ├── main.py                 # FastAPI app + routes
│   ├── config.py               # pydantic-settings
│   ├── schemas.py              # Pydantic models (the output contract)
│   ├── db.py                   # storage layer (SQLite -> Postgres)
│   ├── ingestion/
│   │   └── ingest.py           # CSV validation + load
│   ├── compute/
│   │   └── kpis.py             # DETERMINISTIC math (pure, unit-tested)
│   ├── context/
│   │   ├── store.py            # context CRUD
│   │   └── vector_store.py     # VectorStore interface + FAISS impl
│   ├── retrieval/
│   │   └── retrieve.py         # top-k context retrieval
│   ├── generation/
│   │   ├── llm_client.py       # LLMClient interface + provider impl
│   │   ├── prompts.py          # prompt templates
│   │   ├── generate.py         # facts + context -> narrative
│   │   └── guard.py            # numeric-faithfulness guard
│   └── digest/
│       └── digest.py           # executive summary
├── ui/
│   └── streamlit_app.py        # thin demo UI
├── eval/
│   ├── fixtures/               # golden set
│   └── run.py                  # eval harness (Section 9)
├── tests/
│   ├── test_kpis.py            # math correctness
│   ├── test_guard.py           # faithfulness guard
│   └── test_retrieval.py
└── data/
    └── sample_fpa.csv          # seed dataset
```

---

## 11. Prompt Design (generation layer)

The generation prompt must:
1. State the metric, period, and **all computed facts** explicitly as structured input.
2. Provide the retrieved context chunks, each labeled with its source id and type.
3. Instruct: explain the movement in 2–4 sentences using ONLY the provided numbers; attribute causes ONLY to provided context; if context is insufficient, say the movement is unexplained rather than inventing a cause.
4. Forbid introducing any number not in the facts.
5. Request a machine-parseable structure (narrative + list of source ids actually used).

Keep prompts in `prompts.py`, versioned, so eval can compare prompt versions.

---

## 12. Milestones (BUILD IN THIS ORDER)

### Milestone 1 — MVP: prove the engine (target: core loop working)
**Goal:** One CSV → computed KPIs → hardcoded-ish narrative for a single metric, no RAG yet.
- US-A1, US-A2 (ingestion + deterministic compute) — with unit tests.
- US-C1 minimal: generate a narrative from computed facts + a couple of manually pasted context strings (no FAISS).
- Implement `guard.py` and test it (US-C1 faithfulness AC).
- Bare FastAPI with `/ingest`, `/compute`, `/generate-insight`.
- **Exit criteria:** POST a CSV, get back a faithful narrative JSON for a metric. Faithfulness guard passing on the golden subset.

### Milestone 2 — v1 RAG: make it a real retrieval system
**Goal:** Add the context knowledge base and semantic retrieval.
- US-B1, US-B2, US-B3 (context CRUD + FAISS embed + retrieval behind `VectorStore` interface).
- Wire retrieval into generation (facts + retrieved context).
- US-C2 (digest), US-D1 (full API + OpenAPI), US-E1 (feedback).
- Build the eval harness (Section 9). Report faithfulness, groundedness, recall@k.
- **Exit criteria:** End-to-end loop with real retrieval; eval report prints all four metrics; `/feedback` persists.

### Milestone 3 — Productize & present
**Goal:** Make it demoable and portfolio-ready.
- Streamlit UI (US-F1, US-F2) matching the Narrative-BI card design.
- Swap SQLite→Supabase and FAISS→pgvector via the interfaces (optional but strong).
- Caching + cost logging.
- README with architecture, design-decision rationale (why deterministic compute, why RAG for context), and the eval results.
- **Exit criteria:** A running demo + a README that tells the story. Deployed (Render/Railway + Vercel) as a stretch.

### Future (do not build now)
Growth/marketing use case, anomaly alerts, scheduled digests, metric Q&A, multi-tenancy.

---

## 13. Success Metrics

| Metric | Definition | Target |
|--------|------------|--------|
| Numeric faithfulness | Reports with zero invented numbers | 100% |
| Groundedness | Causes traceable to retrieved context | ≥ 90% |
| Recall@k | Golden context doc in top-k | ≥ 90% |
| Edit-free acceptance | Narratives accepted without edit | Track & improve |
| Time-to-insight | Manual commentary time vs generated | Qualitative case study in README |

---

## 14. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM invents numbers | Destroys trust | Deterministic compute + faithfulness guard + tests |
| Hallucinated causes | Misleading commentary | Ground strictly in retrieved context; "unexplained" fallback |
| Thin/empty context KB | Weak narratives | Seed the KB; confidence=Low when context sparse |
| Scope creep into other use cases | Nothing ships | Milestones enforce FP&A-only for v1 |
| Sensitive data to LLM | Privacy exposure | Redaction hook; document data flow; keys server-side |
| Vendor lock-in | Rework | LLMClient + VectorStore interfaces |

---

## 15. Open Decisions (resolve before/at build start)
1. **Dataset:** synthesize a realistic 24-month FP&A dataset, or use a public financials set? (Recommend: synthesize — full control over the variance story.)
2. **Embeddings:** local `sentence-transformers` (free) vs provider embeddings (better)? (Recommend: start local, keep swappable.)
3. **Frontend for v1:** stay on Streamlit, or build the Next.js version to lean toward the dev lane?

---

*End of PRD.*
