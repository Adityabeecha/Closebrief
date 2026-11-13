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
