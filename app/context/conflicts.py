"""Context-conflict detection (mockup feature: "the March pricing memo and
Q1 churn analysis cite different churn figures").

Deterministic heuristic, no LLM: two documents conflict when they
(a) apply to at least one common metric (shared tag, or either is untagged),
(b) talk about the same thing (meaningful word overlap between bodies), and
(c) each contains a number of similar magnitude that differs beyond rounding
    (>2% but <10x — wildly different magnitudes are different facts, not a
    conflict).

Returned pairs carry the differing figures so the UI banner can show them.
"""

import re

from app.generation.guard import extract_numbers
from app.schemas import ContextDoc

_WORD_RE = re.compile(r"[a-z]{4,}")
_STOPWORDS = {
    "with", "that", "this", "from", "were", "have", "been", "will",
    "monthly", "month", "percent", "figure", "figures", "over", "under",
}


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def _tags_overlap(a: ContextDoc, b: ContextDoc) -> bool:
    if not a.metric_tags or not b.metric_tags:
        return True  # untagged docs are global
    return bool(set(a.metric_tags) & set(b.metric_tags))


def _conflicting_figures(a_text: str, b_text: str) -> list[tuple[float, float]]:
    a_nums = [v for v, _pct in extract_numbers(a_text)]
    b_nums = [v for v, _pct in extract_numbers(b_text)]
    conflicts = []
    for x in a_nums:
        for y in b_nums:
            if x == 0 or y == 0:
                continue
            ratio = max(abs(x), abs(y)) / min(abs(x), abs(y))
            if 1.02 < ratio < 10:
                conflicts.append((x, y))
    return conflicts


def find_conflicts(docs: list[ContextDoc], min_word_overlap: int = 3) -> list[dict]:
    conflicts = []
    for i, a in enumerate(docs):
        for b in docs[i + 1:]:
            if not _tags_overlap(a, b):
                continue
            overlap = _content_words(a.body) & _content_words(b.body)
            if len(overlap) < min_word_overlap:
                continue
            figures = _conflicting_figures(a.body, b.body)
            if not figures:
                continue
            newer = a if (a.effective_date or "") >= (b.effective_date or "") else b
            conflicts.append(
                {
                    "doc_a": {"id": a.id, "title": a.title},
                    "doc_b": {"id": b.id, "title": b.title},
                    "figures": [{"a": x, "b": y} for x, y in figures[:3]],
                    "most_recent": newer.title,
                }
            )
    return conflicts
