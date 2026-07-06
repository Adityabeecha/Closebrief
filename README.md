# Closebrief — Narrative BI

Executive commentary on your KPIs, the moment the books close. Closebrief ingests monthly financials, computes KPI variance deterministically, retrieves your curated business context, and writes grounded, audit-ready variance commentary.

**The core rule:** the LLM never computes numbers. All KPI values, deltas, and trends are computed in pandas ([app/compute/kpis.py](app/compute/kpis.py)); the LLM only phrases prose around pre-computed facts, and every generated number is verified by the faithfulness guard ([app/generation/guard.py](app/generation/guard.py)) before it leaves the system.

## Architecture

```
CSV ─► Ingestion ─► KPI Compute (pandas, deterministic)
                          │ computed facts
context docs ─► OpenAI embeddings ─► FAISS ─► top-k retrieval ──┤
                          ▼                                     ▼
                Narrative Generator (LLM: prose only)
                          ▼
                Numeric-faithfulness guard (reject/flag)
                          ▼
                Structured JSON ─► FastAPI (OpenAPI docs at /docs)
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
copy .env.example .env           # then put your OPENAI_API_KEY in .env
```

One OpenAI key powers both narrative generation (`OPENAI_MODEL`, default gpt-4o) and embeddings (`OPENAI_EMBEDDING_MODEL`, default text-embedding-3-small). The provider is swappable: set `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` to use Claude instead (interfaces in [app/generation/llm_client.py](app/generation/llm_client.py) and [app/context/embeddings.py](app/context/embeddings.py)). Without any key, generation degrades gracefully to facts-only responses and embeddings fall back to a deterministic offline hasher.

## Run

```bash
uvicorn app.main:app --reload
```

Then exercise the loop (sample dataset included):

```bash
# 1. ingest 24 months x 10 metrics of synthetic FP&A data
curl -X POST http://127.0.0.1:8000/ingest -F "file=@data/sample_fpa.csv"

# 2. compute all KPI facts deterministically
curl -X POST http://127.0.0.1:8000/compute

# 3. add business context (embedded into FAISS automatically)
curl -X POST http://127.0.0.1:8000/context -H "Content-Type: application/json" -d "{\"type\":\"event_note\",\"title\":\"March 2025 pricing change\",\"body\":\"On March 1 2025 we raised enterprise prices 15%...\",\"metric_tags\":[\"Net Revenue\"],\"effective_date\":\"2025-03\"}"

# 4. generate a grounded narrative (context auto-retrieved top-k)
curl -X POST http://127.0.0.1:8000/generate-insight -H "Content-Type: application/json" -d "{\"metric\":\"Net Revenue\",\"period\":\"2025-03\"}"

# 5. executive digest: top movements by budget variance
curl -X POST "http://127.0.0.1:8000/digest?period=2025-03&top_n=5"

# 6. analyst feedback loop
curl -X POST http://127.0.0.1:8000/feedback -H "Content-Type: application/json" -d "{\"report_id\":1,\"action\":\"accepted\"}"
```

Interactive API docs: http://127.0.0.1:8000/docs

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /ingest` | Upload CSV (`period, metric, value, budget`); 422 with the missing column named on bad files |
| `POST /compute` | Deterministic KPI math: value, MoM, YoY, budget variance, 12-mo trend, anomaly flag (z-score) |
| `POST /context`, `GET /context`, `PUT/DELETE /context/{id}` | Context Library CRUD; embeds on create/edit, removes vector on delete; FAISS index persists to disk |
| `POST /generate-insight` | Facts + retrieved context → 2–4 sentence narrative with sources, confidence, faithfulness verdict; persisted with a `report_id`; LLM failure returns facts-only 503, never a bare 500 |
| `POST /digest` | Top-N movements for a period ranked by absolute budget variance |
| `POST /feedback` | accept / edit / reject a narrative, linked to its report |

## Faithfulness guard

Every number in a generated narrative is parsed (handles `$4.2M`, `370,000`, `12%`) and matched against the computed facts within rounding tolerance — percent figures only match percent facts, magnitudes only match magnitudes. On failure the system regenerates once with a stricter prompt; if it still fails, the narrative is flagged `faithfulness: failed` and confidence is downgraded. Nothing unverified passes silently.

## Tests

```bash
pytest -q     # 27 tests: KPI math, guard number-extraction/matching, retrieval filters & persistence
```

## Project layout

Matches the PRD (Section 10): `app/ingestion`, `app/compute`, `app/context` (store, embeddings, FAISS vector store), `app/retrieval`, `app/generation` (LLM client, prompts, generate, guard), `app/digest`, `tests/`, `data/` (sample dataset + generator), `eval/` (harness — next milestone).

A graphify knowledge graph of the codebase lives in `graphify-out/` (`graphify query "<question>"` to navigate without re-reading files).

## Status / roadmap

- ✅ Milestone 1 — deterministic engine + guarded generation + API
- ✅ Milestone 2 — RAG core: Context Library, FAISS retrieval, digest, feedback
- ✅ Eval harness (`python -m eval.run`, add `--no-llm` for retrieval-only): **Recall@5 100% · Faithfulness 100% · Groundedness 100%** on a 15-case golden set ([eval/fixtures/golden.json](eval/fixtures/golden.json))
- ✅ Web UI at `/` — Insights Dashboard (cards with deltas, narratives, source chips, confidence + verified-numbers badges, 👍/👎/edit feedback), Executive Digest, Context Library CRUD, CSV import ([ui/web/index.html](ui/web/index.html), design per `ui/mockup/`)
- ⬜ Milestone 3 remainder — Supabase/pgvector swap, caching & cost logging, deploy
