"""Context document CRUD (US-B1) that keeps the vector index in sync (US-B2):
embed on create/edit, remove the vector on delete. The DB is the source of
truth; the FAISS index is a derived structure keyed by document id.
"""

import json
import sqlite3

from app.context.embeddings import Embedder
from app.context.vector_store import VectorStore
from app.schemas import ContextDoc, ContextDocIn


def _embed_text(doc: ContextDocIn) -> str:
    """The text we embed for a document: title + body carries the semantics."""
    return f"{doc.title}\n{doc.body}".strip()


def _row_to_doc(row: sqlite3.Row) -> ContextDoc:
    return ContextDoc(
        id=row["id"],
        type=row["type"],
        title=row["title"],
        body=row["body"],
        metric_tags=json.loads(row["metric_tags"]) if row["metric_tags"] else [],
        effective_date=row["effective_date"],
        # SQLite stores created_at as TEXT; Postgres returns datetime — normalize.
        created_at=str(row["created_at"]) if row["created_at"] is not None else None,
    )


class ContextStore:
    def __init__(
        self, conn: sqlite3.Connection, embedder: Embedder, vector_store: VectorStore
    ) -> None:
        self._conn = conn
        self._embedder = embedder
        self._vs = vector_store

    def add(self, doc: ContextDocIn) -> ContextDoc:
        cur = self._conn.execute(
            """
            INSERT INTO context_documents (type, title, body, metric_tags, effective_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                doc.type,
                doc.title,
                doc.body,
                json.dumps(doc.metric_tags),
                doc.effective_date,
            ),
        )
        self._conn.commit()
        doc_id = int(cur.lastrowid)

        vector = self._embedder.embed([_embed_text(doc)])
        self._vs.add([doc_id], vector)
        self._vs.persist()

        return self.get(doc_id)

    def get(self, doc_id: int) -> ContextDoc | None:
        row = self._conn.execute(
            "SELECT * FROM context_documents WHERE id = ?", (doc_id,)
        ).fetchone()
        return _row_to_doc(row) if row else None

    def list(self) -> list[ContextDoc]:
        rows = self._conn.execute(
            "SELECT * FROM context_documents ORDER BY id"
        ).fetchall()
        return [_row_to_doc(r) for r in rows]

    def update(self, doc_id: int, doc: ContextDocIn) -> ContextDoc | None:
        if self.get(doc_id) is None:
            return None
        self._conn.execute(
            """
            UPDATE context_documents
            SET type=?, title=?, body=?, metric_tags=?, effective_date=?
            WHERE id=?
            """,
            (
                doc.type,
                doc.title,
                doc.body,
                json.dumps(doc.metric_tags),
                doc.effective_date,
                doc_id,
            ),
        )
        self._conn.commit()

        # Re-embed on edit (US-B2).
        vector = self._embedder.embed([_embed_text(doc)])
        self._vs.add([doc_id], vector)
        self._vs.persist()

        return self.get(doc_id)

    def delete(self, doc_id: int) -> bool:
        if self.get(doc_id) is None:
            return False
        self._conn.execute("DELETE FROM context_documents WHERE id = ?", (doc_id,))
        self._conn.commit()
        self._vs.remove([doc_id])
        self._vs.persist()
        return True

    def reindex_all(self) -> int:
        """Rebuild every vector from the DB. Useful after switching embedder
        or restoring a DB without its index."""
        docs = self.list()
        if not docs:
            return 0
        vectors = self._embedder.embed([_embed_text(d) for d in docs])
        self._vs.add([d.id for d in docs], vectors)
        self._vs.persist()
        return len(docs)
