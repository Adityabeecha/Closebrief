"""pgvector implementation of the VectorStore interface (v1.1 Phase 2).

Embeddings live in the `context_vectors` table next to the rest of the data —
one database instead of a Postgres + FAISS-file pair. Vectors are
L2-normalized upstream, so cosine distance (`<=>`) gives the same ranking the
FAISS inner-product index did; score = 1 - distance matches FAISS scores.

The `dim` column guards against comparing vectors from different embedding
models: search filters on the store's dimension, and a model switch simply
reindexes (ContextStore.reindex_all) into the new dimension.
"""

import numpy as np

from app.db import get_connection


def _to_pgvector(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"


_ALLOWED_TABLES = {"context_vectors", "eval_context_vectors"}


class PgVectorStore:
    backend = "pgvector"

    def __init__(self, dim: int, table: str = "context_vectors") -> None:
        if table not in _ALLOWED_TABLES:
            raise ValueError(f"Unknown vector table {table!r}")
        self.dim = dim
        self._table = table

    def add(self, ids: list[int], vectors: np.ndarray) -> None:
        if len(ids) == 0:
            return
        conn = get_connection()
        try:
            for doc_id, vec in zip(ids, np.asarray(vectors, dtype=np.float32)):
                conn.execute(
                    """
                    INSERT INTO context_vectors (context_id, embedding, dim)
                    VALUES (?, ?::vector, ?)
                    ON CONFLICT (context_id) DO UPDATE
                        SET embedding = excluded.embedding, dim = excluded.dim
                    """,
                    (int(doc_id), _to_pgvector(vec), self.dim),
                )
            conn.commit()
        finally:
            conn.close()

    def remove(self, ids: list[int]) -> None:
        if len(ids) == 0:
            return
        conn = get_connection()
        try:
            for doc_id in ids:
                conn.execute(
                    "DELETE FROM context_vectors WHERE context_id = ?", (int(doc_id),)
                )
            conn.commit()
        finally:
            conn.close()

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT context_id, 1 - (embedding <=> ?::vector) AS score
                FROM context_vectors
                WHERE dim = ?
                ORDER BY embedding <=> ?::vector
                LIMIT ?
                """,
                (
                    _to_pgvector(np.asarray(query, dtype=np.float32).reshape(-1)),
                    self.dim,
                    _to_pgvector(np.asarray(query, dtype=np.float32).reshape(-1)),
                    int(k),
                ),
            ).fetchall()
            return [(int(r["context_id"]), float(r["score"])) for r in rows]
        finally:
            conn.close()

    @property
    def size(self) -> int:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM context_vectors WHERE dim = ?", (self.dim,)
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()

    def persist(self) -> None:
        """No-op: Postgres is the persistence."""
