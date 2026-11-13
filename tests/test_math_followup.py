"""Regressions for verified math and vector-update audit findings."""

import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from app.compute.formula import evaluate
from app.compute.funnel import compute_funnel
from app.context.embeddings import HashingEmbedder
from app.context.vector_store import FaissVectorStore
from app.retrieval.retrieve import build_query, retrieve
from app.schemas import ContextDoc


def _funnel_db(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE metrics (id INTEGER PRIMARY KEY, dataset_id INTEGER, name TEXT);
        CREATE TABLE computed_facts (metric_id INTEGER, period TEXT, value REAL);
        INSERT INTO metrics VALUES (1, 1, 'Views'), (2, 1, 'Clicks'), (3, 1, 'Sales');
    """)
    conn.executemany("INSERT INTO computed_facts VALUES (?, ?, ?)", rows)
    return conn


def test_funnel_mom_does_not_compare_across_missing_month():
    conn = _funnel_db([(1, "2025-01", 100), (2, "2025-01", 10),
                       (1, "2025-03", 100), (2, "2025-03", 20)])
    try:
        result = compute_funnel(conn, 1, "2025-03", ["Views", "Clicks"])
        assert result["prior_period"] is None
        assert result["stages"][1]["conversion_mom_pp"] is None
    finally:
        conn.close()


def test_funnel_mom_requires_same_stage_transition():
    conn = _funnel_db([(1, "2025-01", 100), (3, "2025-01", 10),
                       (1, "2025-02", 100), (2, "2025-02", 20), (3, "2025-02", 10)])
    try:
        result = compute_funnel(conn, 1, "2025-02", ["Views", "Clicks", "Sales"])
        assert result["stages"][2]["conversion_from_prev"] == 50
        assert result["stages"][2]["conversion_mom_pp"] is None
    finally:
        conn.close()


@pytest.mark.parametrize("formula,values", [
    ("[Revenue] * 1e308", {"Revenue": 10}),
    ("[Revenue] + 1", {"Revenue": float("inf")}),
    ("[Revenue] + 1", {"Revenue": float("nan")}),
    ("[Revenue] / 1e-308", {"Revenue": 1e308}),
])
def test_nonfinite_formula_result_is_unavailable(formula, values):
    assert evaluate(formula, values) is None


@pytest.mark.parametrize("replacement", [np.ones((1, 3)), np.ones((2, 2))])
def test_invalid_vector_upsert_preserves_existing_vector(tmp_path, replacement):
    store = FaissVectorStore(2, str(tmp_path / "vectors.index"))
    store.add([7], np.array([[1, 0]], dtype=np.float32))
    with pytest.raises((ValueError, AssertionError)):
        store.add([7], replacement)
    assert store.search(np.array([1, 0], dtype=np.float32), 1) == [(7, 1.0)]


def test_retrieval_finds_authorized_context_below_other_workspace_hits(tmp_path):
    embedder = HashingEmbedder(16)
    vectors = FaissVectorStore(16, str(tmp_path / "retrieval.index"))
    query = embedder.embed([build_query("Revenue", "2025-02")])[0]
    vectors.add(list(range(1, 7)), np.array([query] * 5 + [query * 0.5]))
    # The five higher-scoring documents belong to other workspaces and are
    # correctly absent from this caller's scoped context store.
    doc = ContextDoc(id=6, type="event_note", title="Local context", body="Revenue grew", metric_tags=["Revenue"])
    store = SimpleNamespace(list=lambda: [doc])
    assert [chunk.id for chunk in retrieve("Revenue", "2025-02", store, embedder, vectors, k=1)] == [6]


def test_zero_requested_chunks_returns_empty(tmp_path):
    embedder = HashingEmbedder(16)
    vectors = FaissVectorStore(16, str(tmp_path / "retrieval.index"))
    vectors.add([1], embedder.embed(["Revenue"]))
    assert retrieve("Revenue", "2025-02", SimpleNamespace(list=lambda: []), embedder, vectors, k=0) == []
