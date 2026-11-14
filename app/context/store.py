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


def _row_get(row, key):
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _row_dataset(row) -> int | None:
    try:
        value = row["dataset_id"]
    except (IndexError, KeyError):
        return None
    return int(value) if value is not None else None


def _row_to_doc(row: sqlite3.Row) -> ContextDoc:
    dataset_id = _row_dataset(row)
    return ContextDoc(
        dataset_id=dataset_id,
        scope="dataset" if dataset_id is not None else "workspace",
        effective_until=_row_get(row, "effective_until"),
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

    def _dataset_for(self, doc: ContextDocIn, current: int | None = None) -> int | None:
        """A note scoped to a dataset keeps the dataset it was written for; a new one
        takes the dataset open now. Workspace-wide notes have none."""
        if doc.scope == "workspace":
            return None
        if current is not None:
            return current
        return self._open_dataset()

    def _open_dataset(self) -> int | None:
        from app.datasets import active_dataset_id

        try:
            return active_dataset_id(self._conn)
        except Exception:  # noqa: BLE001 - a bare store (no datasets table) has no open dataset
            return None

    def add(self, doc: ContextDocIn, dataset_id: int | None = None) -> ContextDoc:
        """dataset_id: the dataset the note belongs to when it isn't the open one
        (notes created by an import for the dataset it just made)."""
        from app.datasets import current_workspace

        cur = self._conn.execute(
            """
            INSERT INTO context_documents (type, title, body, metric_tags, effective_date, workspace_id, dataset_id,
                                           effective_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.type,
                doc.title,
                doc.body,
                json.dumps(doc.metric_tags),
                doc.effective_date,
                current_workspace(),
                self._dataset_for(doc, dataset_id),
                doc.effective_until,
            ),
        )
        self._conn.commit()
        doc_id = int(cur.lastrowid)

        vector = self._embedder.embed([_embed_text(doc)])
        self._vs.add([doc_id], vector)
        self._vs.persist()

        return self.get(doc_id)

    def get(self, doc_id: int) -> ContextDoc | None:
        from app.datasets import _scope_pred
        row = self._conn.execute(
            f"SELECT * FROM context_documents WHERE id = ? AND {_scope_pred()}", (doc_id,)
        ).fetchone()
        return _row_to_doc(row) if row else None

    def list(self, dataset_id: int | None = None, *, all_datasets: bool = False) -> list[ContextDoc]:
        """Notes for one dataset: its own plus workspace-wide ones (dataset_id NULL).

        Scoped to the caller's universe: demo sessions see only demo-seeded docs;
        real sessions never see them. Retrieval, conflict detection and the reason
        check all build from list(), so a note written for one dataset never explains
        another dataset's numbers. Without a dataset_id the open dataset is used."""
        from app.datasets import _scope_pred

        rows = self._conn.execute(
            f"SELECT * FROM context_documents WHERE {_scope_pred()} ORDER BY id"
        ).fetchall()
        docs = [_row_to_doc(r) for r in rows]
        if all_datasets:
            return docs
        ds = dataset_id if dataset_id is not None else self._open_dataset()
        return [d for d in docs if d.dataset_id is None or d.dataset_id == ds]

    def update(self, doc_id: int, doc: ContextDocIn) -> ContextDoc | None:
        existing = self.get(doc_id)
        if existing is None:
            return None
        self._conn.execute(
            """
            UPDATE context_documents
            SET type=?, title=?, body=?, metric_tags=?, effective_date=?, dataset_id=?, effective_until=?
            WHERE id=?
            """,
            (
                doc.type,
                doc.title,
                doc.body,
                json.dumps(doc.metric_tags),
                doc.effective_date,
                self._dataset_for(doc, existing.dataset_id),
                doc.effective_until,
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
        docs = self.list(all_datasets=True)
        if not docs:
            return 0
        vectors = self._embedder.embed([_embed_text(d) for d in docs])
        self._vs.add([d.id for d in docs], vectors)
        self._vs.persist()
        return len(docs)
