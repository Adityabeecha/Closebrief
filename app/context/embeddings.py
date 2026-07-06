"""Embedder interface + implementations.

Kept behind an interface (PRD: embeddings must be "swappable behind an
interface") so the OpenAI provider can be replaced — or, in tests, a
deterministic hash-based fake used — without touching retrieval or storage.
"""

import hashlib
from typing import Protocol

import numpy as np

from app.config import settings


class Embedder(Protocol):
    dim: int
    semantic: bool  # True if similarity scores are meaningful (drive confidence)

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 array of L2-normalized embeddings."""
        ...


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


class OpenAIEmbedder:
    """Default embedder, backed by the OpenAI embeddings API."""

    semantic = True  # scores are meaningful -> may drive High confidence
    _DIMS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, model: str | None = None) -> None:
        import openai

        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env to enable embeddings."
            )
        self._client = openai.OpenAI(api_key=settings.openai_api_key)
        self._model = model or settings.openai_embedding_model
        self.dim = self._DIMS.get(self._model, 1536)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        resp = self._client.embeddings.create(model=self._model, input=texts)
        vectors = np.array([d.embedding for d in resp.data], dtype=np.float32)
        return _l2_normalize(vectors)


class HashingEmbedder:
    """Deterministic, offline embedder for tests and local runs without a key.
    Not semantically meaningful, but stable and normalized — good enough to
    exercise the retrieval/index code paths without network calls."""

    semantic = False  # scores are noise -> confidence must not exceed Medium

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for token in text.lower().split():
                h = int(hashlib.md5(token.encode()).hexdigest(), 16)
                vectors[i, h % self.dim] += 1.0
        return _l2_normalize(vectors)


def get_embedder() -> Embedder:
    if not settings.openai_api_key:
        # Fall back to the offline embedder so the system still runs end-to-end
        # (retrieval works, just without semantic quality) when no key is set.
        return HashingEmbedder()
    return OpenAIEmbedder()
