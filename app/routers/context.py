"""HTTP routes moved from app.main: context."""

from fastapi import APIRouter, Depends, HTTPException

from app import audit
from app.api import require_read, require_write  # noqa: E402
from app.auth import CurrentUser
from app.context.conflicts import find_conflicts
from app.datasets import active_dataset_id
from app.db import get_connection
from app.deps import shared_cache
from app.routes_common import _context_store
from app.schemas import (
    ContextDoc,
    ContextDocIn,
)

router = APIRouter()


@router.get("/context/conflicts")
def context_conflicts(_: CurrentUser = Depends(require_read)) -> list[dict]:
    """Pairs of context documents citing different figures about the same
    thing (mockup's conflict banner). Deterministic heuristic, no LLM."""
    conn = get_connection()
    try:
        docs = _context_store(conn).list()
        ds = active_dataset_id(conn)
        names = {str(r["name"]).strip().lower() for r in conn.execute(
            "SELECT name FROM metrics WHERE dataset_id = ?", (ds,)).fetchall()} if ds is not None else set()
        names |= {str(r["display_name"]).strip().lower() for r in conn.execute(
            "SELECT display_name FROM kpi_configs WHERE dataset_id = ? AND display_name IS NOT NULL", (ds,)).fetchall()} if ds is not None else set()
    finally:
        conn.close()
    # Only notes about this dataset's KPIs: a churn memo's conflict means nothing on a P&L.
    relevant = [d for d in docs if not d.metric_tags
                or {str(t).strip().lower() for t in d.metric_tags} & names]
    return find_conflicts(relevant)


@router.post("/context", response_model=ContextDoc, status_code=201)
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


@router.get("/context", response_model=list[ContextDoc])
def list_context(_: CurrentUser = Depends(require_read)) -> list[ContextDoc]:
    conn = get_connection()
    try:
        return _context_store(conn).list()
    finally:
        conn.close()


@router.put("/context/{doc_id}", response_model=ContextDoc)
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


@router.delete("/context/{doc_id}", status_code=204)
def delete_context(doc_id: int, _: CurrentUser = Depends(require_write)) -> None:
    conn = get_connection()
    try:
        deleted = _context_store(conn).delete(doc_id)
    finally:
        conn.close()
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Context document {doc_id} not found")
    shared_cache().bump_namespace()


@router.post("/context/extract")
def extract_context(payload: dict, user: CurrentUser = Depends(require_write)) -> dict:
    """Propose notes from pasted text (earnings release, MD&A, variance memo): one per KPI
    it explains, tagged with this dataset's KPI names. Nothing is saved; the analyst keeps
    the ones they want with POST /context."""
    import time

    from app.config import settings
    from app.context.extract import EXTRACT_SYSTEM_PROMPT, build_prompt, parse_proposals
    from app.generation.llm_client import LLMGenerationError, get_llm_client
    from app.services import enforce_budget, log_llm_call

    text = str(payload.get("text") or "").strip()
    period = payload.get("period")
    if len(text) < 20:
        raise HTTPException(status_code=422, detail="Paste the text to read: an earnings release, MD&A or variance memo.")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=409, detail="Import data first, so notes can be tagged with your KPIs.")
        kpis = sorted({r["name"] for r in conn.execute(
            """SELECT COALESCE(k.display_name, m.name) AS name FROM metrics m
               LEFT JOIN kpi_configs k ON k.dataset_id = m.dataset_id AND k.source_metric = m.name
               WHERE m.dataset_id = ?""", (ds,)).fetchall()} | {r["name"] for r in conn.execute(
            "SELECT name FROM metrics WHERE dataset_id = ?", (ds,)).fetchall()})
    finally:
        conn.close()
    from app.datasets import current_workspace
    from app.privacy import Redactor, load_terms

    conn = get_connection()
    try:
        redactor = Redactor(load_terms(conn, current_workspace()))
    finally:
        conn.close()
    enforce_budget(user)
    t0 = time.monotonic()
    try:
        result, usage = get_llm_client().generate_narrative(EXTRACT_SYSTEM_PROMPT,
                                                            build_prompt(redactor.hide(text), kpis, period))
    except LLMGenerationError as exc:
        raise HTTPException(status_code=503, detail=f"Couldn't read the text right now: {exc}") from exc
    log_llm_call("/context/extract",
                 settings.openai_model if settings.llm_provider == "openai" else settings.anthropic_model,
                 usage.prompt_tokens, usage.completion_tokens, usage.cost_usd,
                 int((time.monotonic() - t0) * 1000), user.id)
    proposals = parse_proposals(result.narrative, kpis, period)
    for p in proposals:
        p["title"], p["body"] = redactor.show(p["title"]), redactor.show(p["body"])
    return {"proposals": proposals, "kpis": kpis}
