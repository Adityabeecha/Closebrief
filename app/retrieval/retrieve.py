"""Top-k context retrieval (US-B3).

Given a metric + period, embed a query, search the vector store, then apply
business filters: keep a chunk only if (a) it has no metric_tags or is tagged
for this metric, and (b) its effective_date (if any) is on or before the
period. Never returns more than k chunks (token/cost control).
"""

from app.context.embeddings import Embedder
from app.context.store import ContextStore
from app.context.vector_store import VectorStore
from app.schemas import ContextDoc, RetrievedChunk


def _period_key(period: str) -> str:
    """Normalize 'YYYY-MM' or 'YYYY-MM-DD' to a comparable 'YYYY-MM' key."""
    return period[:7]


def _effective_ok(doc: ContextDoc, period: str) -> bool:
    if not doc.effective_date:
        return True
    return _period_key(doc.effective_date) <= _period_key(period)


def _metric_ok(doc: ContextDoc, metric: str) -> bool:
    if not doc.metric_tags:
        return True  # untagged docs are globally relevant
    return metric in doc.metric_tags


def build_query(metric: str, period: str) -> str:
    return f"Variance commentary for {metric} in {period}. Why did {metric} move?"


def retrieve(
    metric: str,
    period: str,
    store: ContextStore,
    embedder: Embedder,
    vector_store: VectorStore,
    k: int = 5,
) -> list[RetrievedChunk]:
    query_vec = embedder.embed([build_query(metric, period)])
    if query_vec.shape[0] == 0:
        return []

    # Over-fetch before filtering so post-filter we can still return up to k.
    raw_hits = vector_store.search(query_vec[0], k=k * 4)

    docs_by_id = {d.id: d for d in store.list()}
    chunks: list[RetrievedChunk] = []
    for doc_id, score in raw_hits:
        doc = docs_by_id.get(doc_id)
        if doc is None:
            continue
        if not _metric_ok(doc, metric) or not _effective_ok(doc, period):
            continue
        chunks.append(
            RetrievedChunk(
                id=doc.id, type=doc.type, title=doc.title, body=doc.body, score=score
            )
        )
        if len(chunks) >= k:
            break
    return chunks
