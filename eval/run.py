"""Eval harness (PRD Section 9). Run: python -m eval.run [--no-llm]

Metrics:
  1. Numeric faithfulness  — every number in each generated narrative maps to a
                             computed fact (guard-verified). Target: 100%.
  2. Groundedness          — cause keywords in the narrative appear in a
                             retrieved context chunk (keyword overlap method).
  3. Recall@k              — the expected golden context doc(s) appear in the
                             top-k retrieved for each case.
  4. Acceptance proxy      — edit-free acceptance rate from the feedback table
                             (skipped if the app DB has no feedback yet).

--no-llm skips generation-dependent metrics (1, 2) and runs retrieval-only.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from app.context.embeddings import get_embedder
from app.context.store import ContextStore
from app.context.vector_store import FaissVectorStore
from app.generation.guard import check_faithfulness
from app.retrieval.retrieve import retrieve
from app.schemas import ComputedFact, ContextDocIn, ContextSnippet

FIXTURES = Path(__file__).parent / "fixtures" / "golden.json"

CONTEXT_SCHEMA = """
CREATE TABLE context_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
    metric_tags TEXT NOT NULL DEFAULT '', effective_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def load_cases() -> list[dict]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


def _make_vector_store(embedder, case_id: str, tmp_dir: Path, backend: str):
    """FAISS (per-case index file) or pgvector (isolated eval_context_vectors
    table, cleared per case so ids don't collide across cases)."""
    if backend == "pgvector":
        from app.context.pg_vector_store import PgVectorStore
        from app.db import get_connection

        conn = get_connection()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS eval_context_vectors (
                       context_id BIGINT PRIMARY KEY, embedding vector NOT NULL, dim INTEGER NOT NULL)"""
            )
            conn.execute("DELETE FROM eval_context_vectors")
            conn.commit()
        finally:
            conn.close()
        return PgVectorStore(dim=embedder.dim, table="eval_context_vectors")

    return FaissVectorStore(dim=embedder.dim, index_path=str(tmp_dir / f"{case_id}.index"))


def build_case_store(case: dict, tmp_dir: Path, backend: str = "faiss"):
    """Fresh in-memory context store per case so each case's library is isolated.
    Context metadata always lives in in-memory SQLite; only the vector backend
    varies (FAISS vs pgvector) so retrieval parity can be compared."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(CONTEXT_SCHEMA)
    embedder = get_embedder()
    vs = _make_vector_store(embedder, case["id"], tmp_dir, backend)
    store = ContextStore(conn, embedder, vs)
    title_to_id: dict[str, int] = {}
    for doc in case["context_docs"]:
        added = store.add(ContextDocIn(**doc))
        title_to_id[added.title] = added.id
    return store, embedder, vs, title_to_id


def eval_retrieval(cases: list[dict], tmp_dir: Path, k: int = 5, backend: str = "faiss") -> tuple[float, list[str]]:
    """Recall@k over cases that expect at least one context doc."""
    hits, total, failures = 0, 0, []
    for case in cases:
        expected = case.get("expected_context_titles", [])
        if not expected:
            continue
        store, embedder, vs, _ = build_case_store(case, tmp_dir, backend)
        fact = case["fact"]
        chunks = retrieve(fact["metric"], fact["period"], store, embedder, vs, k=k)
        got_titles = {c.title for c in chunks}
        for title in expected:
            total += 1
            if title in got_titles:
                hits += 1
            else:
                failures.append(f"{case['id']}: expected '{title}' not in top-{k} {sorted(got_titles)}")
        for title in case.get("excluded_context_titles", []):
            total += 1
            if title not in got_titles:
                hits += 1
            else:
                failures.append(f"{case['id']}: excluded '{title}' WAS retrieved (effective_date filter)")
    return (hits / total if total else 1.0), failures


def eval_generation(cases: list[dict], tmp_dir: Path, k: int = 5, backend: str = "faiss"):
    """Generate a narrative per case; measure faithfulness + groundedness."""
    from app.generation.generate import GenerationFailedFactsOnly, generate_insight
    from app.generation.llm_client import get_llm_client

    llm = get_llm_client()
    faithful, grounded, gen_total = 0, 0, 0
    faith_failures, ground_failures = [], []
    narratives: dict[str, str] = {}

    for case in cases:
        fact = ComputedFact(**case["fact"])
        store, embedder, vs, _ = build_case_store(case, tmp_dir, backend)
        chunks = retrieve(fact.metric, fact.period, store, embedder, vs, k=k)
        context = [
            ContextSnippet(id=f"ctx_{c.id:03d}", type=c.type, title=c.title, body=c.body)
            for c in chunks
        ]
        try:
            insight = generate_insight(fact, context, llm, [c.score for c in chunks])
        except GenerationFailedFactsOnly:
            faith_failures.append(f"{case['id']}: LLM unavailable")
            continue

        gen_total += 1
        narratives[case["id"]] = insight.narrative or ""

        passed, unverified = check_faithfulness(insight.narrative or "", fact, context)
        if passed:
            faithful += 1
        else:
            faith_failures.append(f"{case['id']}: unverified numbers {unverified}")

        # Groundedness (keyword overlap): any cause keyword used by the
        # narrative must exist in a retrieved chunk; if the case has no
        # context, the narrative must not claim causes from those keywords.
        keywords = [kw.lower() for kw in case.get("cause_keywords", [])]
        narrative_l = (insight.narrative or "").lower()
        chunk_text = " ".join(c.body.lower() + " " + c.title.lower() for c in chunks)
        used = [kw for kw in keywords if kw in narrative_l]
        ungrounded = [kw for kw in used if kw not in chunk_text]
        if not ungrounded:
            grounded += 1
        else:
            ground_failures.append(f"{case['id']}: ungrounded causes {ungrounded}")

    faith_rate = faithful / gen_total if gen_total else 0.0
    ground_rate = grounded / gen_total if gen_total else 0.0
    return faith_rate, ground_rate, faith_failures, ground_failures, narratives


def eval_acceptance() -> str:
    """Edit-free acceptance rate from the live app DB feedback table."""
    from app.db import get_connection

    try:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT
              SUM(CASE WHEN action = 'accepted' THEN 1 ELSE 0 END) AS accepted,
              COUNT(*) AS total
            FROM feedback
            """
        ).fetchone()
        conn.close()
        if not row or not row["total"]:
            return "n/a (no feedback recorded yet)"
        return f"{row['accepted'] / row['total']:.0%} ({row['accepted']}/{row['total']})"
    except Exception:
        return "n/a (app DB not initialized)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Closebrief eval harness")
    parser.add_argument("--no-llm", action="store_true", help="skip generation metrics")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument(
        "--backend", choices=["faiss", "pgvector"], default="faiss",
        help="vector backend to run retrieval against (US-L1 parity check)",
    )
    args = parser.parse_args()

    import tempfile

    cases = load_cases()
    print(f"Closebrief eval — {len(cases)} golden cases, k={args.k}, backend={args.backend}\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        recall, recall_failures = eval_retrieval(cases, tmp_dir, k=args.k, backend=args.backend)
        print(f"Recall@{args.k}:            {recall:.0%}")
        for f in recall_failures:
            print(f"    FAIL {f}")

        if args.no_llm:
            print("Faithfulness:        skipped (--no-llm)")
            print("Groundedness:        skipped (--no-llm)")
        else:
            faith, ground, faith_failures, ground_failures, _ = eval_generation(
                cases, tmp_dir, k=args.k, backend=args.backend
            )
            print(f"Faithfulness:        {faith:.0%}")
            for f in faith_failures:
                print(f"    FAIL {f}")
            print(f"Groundedness:        {ground:.0%}")
            for f in ground_failures:
                print(f"    FAIL {f}")

    print(f"Edit-free acceptance: {eval_acceptance()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
