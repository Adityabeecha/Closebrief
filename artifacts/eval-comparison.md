# Commentary evaluation

Compared the committed v3-number-format prompt with v4-close-commentary using the existing eval.run harness (15 golden commentary cases, k=5), gpt-4o, offline embeddings, and FAISS. Both runs used the same local API configuration; credentials are not included here.

| Metric | Before | After |
| --- | --- | --- |
| Faithfulness | 100% | 100% |
| Recall@5 | 100% | 100% |
| Groundedness | 100% | 100% |

The plain `python -m eval.run` command in this worktree reaches Recall@5=100%, then stops because the worktree has no OPENAI_API_KEY. The comparison loaded the existing main checkout configuration in memory and pinned OPENAI_MODEL=gpt-4o, EMBEDDING_PROVIDER=offline, VECTOR_BACKEND=faiss, matching the repository defaults. The before prompt was loaded from git HEAD without modifying the working files. LLM scores are measurements of these runs, not deterministic guarantees.

The demo board pack is saved alongside this file as demo-board-pack.html. Its commentary sections remain empty because the seeded demo narratives have not been approved; the export includes approved commentary only.

The harness also ran six ancillary Q&A cases (the Q&A prompt was unchanged): before, faithfulness 83% and appropriateness 100%; after, faithfulness 100% and appropriateness 83%. The after run incorrectly declined qa_01_revenue_why.
