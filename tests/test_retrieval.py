import sqlite3

import pytest

from app.context.embeddings import HashingEmbedder
from app.context.store import ContextStore
from app.context.vector_store import FaissVectorStore
from app.retrieval.retrieve import retrieve
from app.schemas import ContextDocIn

SCHEMA = """
CREATE TABLE context_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    metric_tags TEXT NOT NULL DEFAULT '',
    effective_date TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    workspace_id INTEGER,
    dataset_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def store(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    embedder = HashingEmbedder()
    vs = FaissVectorStore(dim=embedder.dim, index_path=str(tmp_path / "test.index"))
    yield ContextStore(conn, embedder, vs), embedder, vs
    conn.close()


def add_doc(store: ContextStore, title: str, body: str, **kw) -> int:
    doc = store.add(ContextDocIn(title=title, body=body, **kw))
    return doc.id


def test_add_and_retrieve_relevant_doc(store):
    ctx_store, embedder, vs = store
    pricing_id = add_doc(
        ctx_store,
        "March pricing change",
        "Net Revenue impacted by enterprise pricing change causing churn",
        metric_tags=["Net Revenue"],
    )
    add_doc(
        ctx_store,
        "Office lease renewal",
        "Facilities lease renewed in Austin office campus",
        metric_tags=["Operating Expenses"],
    )

    chunks = retrieve("Net Revenue", "2025-03", ctx_store, embedder, vs, k=5)
    ids = [c.id for c in chunks]
    assert pricing_id in ids
    # tagged for a different metric -> filtered out
    assert all(c.title != "Office lease renewal" for c in chunks)


def test_effective_date_filter(store):
    ctx_store, embedder, vs = store
    add_doc(
        ctx_store,
        "Future event",
        "Net Revenue will change due to a future pricing event",
        metric_tags=["Net Revenue"],
        effective_date="2025-09",
    )
    ok_id = add_doc(
        ctx_store,
        "Past event",
        "Net Revenue moved due to a past pricing event",
        metric_tags=["Net Revenue"],
        effective_date="2025-01",
    )

    chunks = retrieve("Net Revenue", "2025-03", ctx_store, embedder, vs, k=5)
    ids = [c.id for c in chunks]
    assert ok_id in ids
    assert all(c.title != "Future event" for c in chunks)


def test_untagged_docs_are_global(store):
    ctx_store, embedder, vs = store
    glossary_id = add_doc(
        ctx_store,
        "Glossary",
        "Net Revenue is total revenue minus refunds and discounts",
        type="glossary",
    )
    chunks = retrieve("Net Revenue", "2025-03", ctx_store, embedder, vs, k=5)
    assert glossary_id in [c.id for c in chunks]


def test_never_more_than_k(store):
    ctx_store, embedder, vs = store
    for i in range(8):
        add_doc(ctx_store, f"Note {i}", f"Net Revenue commentary note number {i}")
    chunks = retrieve("Net Revenue", "2025-03", ctx_store, embedder, vs, k=3)
    assert len(chunks) <= 3


def test_delete_removes_from_retrieval(store):
    ctx_store, embedder, vs = store
    doc_id = add_doc(ctx_store, "Churn note", "Net Revenue churn analysis", metric_tags=["Net Revenue"])
    assert doc_id in [c.id for c in retrieve("Net Revenue", "2025-03", ctx_store, embedder, vs)]
    ctx_store.delete(doc_id)
    assert doc_id not in [c.id for c in retrieve("Net Revenue", "2025-03", ctx_store, embedder, vs)]


def test_index_persists_across_restart(store, tmp_path):
    ctx_store, embedder, vs = store
    add_doc(ctx_store, "Persistent note", "Net Revenue seasonal pattern", metric_tags=["Net Revenue"])
    # simulate restart: new FaissVectorStore instance from the same path
    vs2 = FaissVectorStore(dim=embedder.dim, index_path=vs._index_path.as_posix())
    assert vs2.size == vs.size
    chunks = retrieve("Net Revenue", "2025-03", ctx_store, embedder, vs2)
    assert len(chunks) == 1


def test_an_untagged_note_never_explains_a_move(store):
    """Live test: an untagged, undated "Q1 seasonality" note explained an October margin drop."""
    ctx_store, embedder, vs = store
    seasonal = add_doc(ctx_store, "Q1 seasonality", "Q1 bookings are soft as enterprise budgets reset",
                       type="historical_commentary")
    untagged_event = add_doc(ctx_store, "Price change", "Prices rose in October")
    definition = add_doc(ctx_store, "Gross margin definition", "Gross margin is revenue less cost of revenue",
                         type="definition")
    ids = [c.id for c in retrieve("Gross Margin %", "2026-10", ctx_store, embedder, vs, k=5)]
    assert seasonal not in ids and untagged_event not in ids
    assert definition in ids   # background still reaches the draft


def test_an_event_note_is_used_for_its_month_and_the_two_after(store):
    ctx_store, embedder, vs = store
    note = add_doc(ctx_store, "Dreamforce", "Sales & Marketing booth and travel",
                   metric_tags=["sales & marketing"], effective_date="2026-10")
    for month, expected in (("2026-09", False), ("2026-10", True), ("2026-12", True), ("2027-01", False)):
        ids = [c.id for c in retrieve("Sales & Marketing", month, ctx_store, embedder, vs, k=5)]
        assert (note in ids) is expected, month
