"""Shared singletons for the API layer: embedder, vector store, and cache are
built once and reused across requests. DB connections stay per-request (the
Postgres backend hands out pooled connections; SQLite connections are not
thread-safe across requests)."""

from functools import lru_cache

from app.config import resolved_vector_backend
from app.context.embeddings import Embedder, get_embedder


@lru_cache(maxsize=1)
def shared_embedder() -> Embedder:
    return get_embedder()


@lru_cache(maxsize=1)
def shared_vector_store():
    """FAISS locally; pgvector when running against Supabase (v1.1 Phase 2).
    Both implement the VectorStore protocol — config-only swap."""
    if resolved_vector_backend() == "pgvector":
        from app.context.pg_vector_store import PgVectorStore

        return PgVectorStore(dim=shared_embedder().dim)
    from app.context.vector_store import FaissVectorStore

    return FaissVectorStore(dim=shared_embedder().dim)


@lru_cache(maxsize=1)
def shared_cache():
    from app.cache import get_cache

    return get_cache()
