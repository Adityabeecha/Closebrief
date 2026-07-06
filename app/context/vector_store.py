"""VectorStore interface + FAISS implementation.

Per the PRD: "Keep the vector store behind a VectorStore interface so swapping
FAISS -> pgvector is a one-class change, not a rewrite." Vectors are keyed by
the context document's integer id (FAISS IDMap), so add/remove by id maps
directly onto context CRUD.

The index is persisted to disk (US-B2: "Must persist the index to disk so it
survives restart") alongside a small JSON sidecar recording the dimension.
"""

import json
from pathlib import Path
from typing import Protocol

import faiss
import numpy as np


class VectorStore(Protocol):
    def add(self, ids: list[int], vectors: np.ndarray) -> None: ...
    def remove(self, ids: list[int]) -> None: ...
    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]: ...
    def persist(self) -> None: ...


class FaissVectorStore:
    """Inner-product (cosine, since vectors are L2-normalized) FAISS index with
    integer id mapping, persisted to `index_path`."""

    def __init__(self, dim: int, index_path: str = "./data/faiss.index") -> None:
        self.dim = dim
        self._index_path = Path(index_path)
        self._meta_path = self._index_path.with_suffix(".meta.json")
        self._index = self._load_or_create()

    def _load_or_create(self) -> "faiss.IndexIDMap":
        if self._index_path.exists() and self._meta_path.exists():
            meta = json.loads(self._meta_path.read_text())
            if meta.get("dim") == self.dim:
                return faiss.read_index(str(self._index_path))
            # Dimension changed (e.g. switched embedding model) -> rebuild empty.
        base = faiss.IndexFlatIP(self.dim)
        return faiss.IndexIDMap(base)

    def add(self, ids: list[int], vectors: np.ndarray) -> None:
        if len(ids) == 0:
            return
        vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        id_array = np.array(ids, dtype=np.int64)
        # Idempotent upsert: drop any existing vectors for these ids first.
        self._index.remove_ids(id_array)
        self._index.add_with_ids(vectors, id_array)

    def remove(self, ids: list[int]) -> None:
        if len(ids) == 0:
            return
        self._index.remove_ids(np.array(ids, dtype=np.int64))

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self._index.ntotal == 0:
            return []
        query = np.ascontiguousarray(query.reshape(1, -1), dtype=np.float32)
        scores, ids = self._index.search(query, min(k, self._index.ntotal))
        return [
            (int(i), float(s))
            for i, s in zip(ids[0], scores[0])
            if i != -1
        ]

    @property
    def size(self) -> int:
        return int(self._index.ntotal)

    def persist(self) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._index_path))
        self._meta_path.write_text(json.dumps({"dim": self.dim}))
