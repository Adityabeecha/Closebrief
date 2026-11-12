"""HTTP routes moved from app.main: generate."""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app import audit, scheduling
from app.api import require_member, require_read, require_write  # noqa: E402
from app.auth import CurrentUser
from app.board_pack import build_board_pack_html
from app.cache import make_insight_key
from app.close_control import ensure_commentary_unlocked
from app.compute.correlations import (
    correlations_for_metric,
    detect_consecutive_trends,
)
from app.compute.funnel import compute_funnel
from app.config import auth_active, settings
from app.datasets import (
    active_dataset_id,
    is_demo_scope,
)
from app.db import get_connection
from app.deps import shared_cache, shared_embedder, shared_vector_store
from app.digest.digest import DigestOutput, build_approved_digest, last_sent_at
from app.domains import get_domain
from app.generation.generate import GenerationFailedFactsOnly, generate_insight
from app.generation.llm_client import LLMGenerationError, get_llm_client
from app.generation.prompts import (
    FUNNEL_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_funnel_prompt,
)
from app.generation.qa import answer_question
from app.retrieval.retrieve import retrieve
from app.routers.facts import _active_metric_id, _load_computed_fact, list_facts
from app.routes_common import _active_domain_slug, _context_store, _notify_async
from app.scheduling import record_digest_run
from app.schemas import (
    ContextSnippet,
    CorrelationPair,
    GenerateInsightRequest,
    InsightOutput,
    TrendStreak,
)
from app.services import enforce_budget as _enforce_budget  # noqa: E402
from app.services import log_llm_call as _log_llm_call  # noqa: E402

router = APIRouter()


@router.post("/funnel/narrative")
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


@router.post("/generate-insight", response_model=InsightOutput)
def generate_insight_endpoint(req: GenerateInsightRequest, user: CurrentUser = Depends(require_member)) -> InsightOutput:
    conn = get_connection()
    try:
        # Fail fast without the write lock; _persist_report re-checks under it.
        ensure_commentary_unlocked(conn, active_dataset_id(conn), req.period, serialize=False)
        _enforce_budget(user)
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
        ensure_commentary_unlocked(conn, active_dataset_id(conn), insight.period)
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


@router.post("/ask")
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


@router.get("/digest", response_model=DigestOutput)
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


@router.post("/digest/send", response_model=DigestOutput)
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


@router.get("/board-pack")
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


@router.get("/digests/history")
def digest_history(_: CurrentUser = Depends(require_read)) -> list[dict]:
    conn = get_connection()
    try:
        dataset_id = active_dataset_id(conn)
        return scheduling.list_digest_runs(conn, dataset_id) if dataset_id is not None else []
    finally:
        conn.close()
