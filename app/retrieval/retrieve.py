"""Top-k context retrieval (US-B3).

Given a metric + period, embed a query, search the vector store, then apply
business filters. Notes that can be read as the cause of a move (event notes and
historical commentary) must be tagged with this KPI, and a dated event note must be
from this month or the two before it: an untagged "Q1 seasonality" note must never
explain an October margin drop. Untagged notes are background only (definitions,
glossary, policy). Nothing dated after the period is used. Never returns more than
k chunks (token/cost control).
"""

from app.context.embeddings import Embedder
from app.context.store import ContextStore
from app.context.vector_store import VectorStore
from app.schemas import ContextDoc, RetrievedChunk


def _period_key(period: str) -> str:
    """Normalize 'YYYY-MM' or 'YYYY-MM-DD' to a comparable 'YYYY-MM' key."""
    return period[:7]


# Notes that describe what happened (and so read as a reason) versus background.
CAUSAL_TYPES = {"event_note", "historical_commentary"}
EVENT_WINDOW_MONTHS = 3  # this month and the two before it


def _month_index(key: str) -> int | None:
    try:
        return int(key[:4]) * 12 + int(key[5:7]) - 1
    except (TypeError, ValueError):
        return None


def _effective_ok(doc: ContextDoc, period: str) -> bool:
    if not doc.effective_date:
        return True
    eff, per = _period_key(doc.effective_date), _period_key(period)
    if eff > per:
        return False
    if doc.type == "event_note":
        e, p = _month_index(eff), _month_index(per)
        if e is not None and p is not None:
            return p - e < EVENT_WINDOW_MONTHS
    return True


def _tagged_for(doc: ContextDoc, metric: str) -> bool:
    want = str(metric).strip().lower()
    return any(str(t).strip().lower() == want for t in doc.metric_tags or [])


def _metric_ok(doc: ContextDoc, metric: str) -> bool:
    if doc.type in CAUSAL_TYPES:
        return _tagged_for(doc, metric)
    if not doc.metric_tags:
        return True  # an untagged definition or policy is background for every KPI
    return _tagged_for(doc, metric)


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
    if k <= 0:
        return []
    query_vec = embedder.embed([build_query(metric, period)])
    if query_vec.shape[0] == 0:
        return []

    docs_by_id = {d.id: d for d in store.list()
                  if _metric_ok(d, metric) and _effective_ok(d, period)}
    if not docs_by_id:
        return []
    # Other workspaces and ineligible documents can occupy the initial window.
    # Widen until enough eligible hits are found or the index is exhausted.
    fetch_k = k * 4
    while True:
        raw_hits = vector_store.search(query_vec[0], k=fetch_k)
        chunks: list[RetrievedChunk] = []
        for doc_id, score in raw_hits:
            doc = docs_by_id.get(doc_id)
            if doc is None:
                continue
            chunks.append(
                RetrievedChunk(
                    id=doc.id, type=doc.type, title=doc.title, body=doc.body, score=score
                )
            )
            if len(chunks) >= k:
                return chunks
        if len(raw_hits) < fetch_k or len(chunks) == len(docs_by_id):
            return chunks
        fetch_k *= 2
