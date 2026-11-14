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
    "million", "billion", "thousand", "dollars", "reported", "totaled", "totalled",
    "quarter", "year", "fiscal", "grew", "growth", "increased", "decreased",
}
_FIGURE_RE = re.compile(
    r"(?<![\w])(?:[+\-\u2212]?[$€£₹¥]?\d[\d,]*(?:\.\d+)?)"
    r"\s*(?:million|billion|thousand|[mbk](?![a-z]))?\s*(?:%|percent\b|per\s+cent\b)?",
    re.I,
)
_CURRENCY = re.compile(r"[$€£₹¥]|\b(?:USD|EUR|GBP|INR|JPY|dollars?|ARR|MRR)\b", re.I)
_SENTENCE_BREAK = re.compile(r"[!?;\n]|(?<!\d)\.|\.(?!\d)")


def _figures_with_subjects(text: str) -> list[tuple[float, str, set[str]]]:
    """Only typed financial figures, with subject words within six words in the sentence."""
    figures = []
    for sentence in _SENTENCE_BREAK.split(text):
        for match in _FIGURE_RE.finditer(sentence):
            raw = match.group().strip()
            numbers = extract_numbers(raw)
            if not numbers:
                continue
            value, pct = numbers[0]
            after = sentence[match.end():]
            before = sentence[:match.start()]
            currency = (_CURRENCY.search(raw)
                        or re.match(r"\s*(?:USD|EUR|GBP|INR|JPY|dollars?|ARR|MRR)\b", after, re.I)
                        or re.search(r"(?:[$€£₹¥]|\b(?:USD|EUR|GBP|INR|JPY))\s*$", before, re.I))
            if not pct and not currency:
                continue  # bare counts, years and fiscal labels are not amounts
            nearby = " ".join(re.findall(r"\b\w+\b", before)[-6:] + re.findall(r"\b\w+\b", after)[:6])
            figures.append((value, "percent" if pct else "currency", _content_words(nearby)))
    return figures


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def _tags_overlap(a: ContextDoc, b: ContextDoc) -> bool:
    if not a.metric_tags or not b.metric_tags:
        return True  # untagged docs are global
    return bool(set(a.metric_tags) & set(b.metric_tags))


def _conflicting_figures(a_text: str, b_text: str) -> list[tuple[float, float]]:
    a_nums = _figures_with_subjects(a_text)
    b_nums = _figures_with_subjects(b_text)
    conflicts = []
    for x, x_kind, x_words in a_nums:
        for y, y_kind, y_words in b_nums:
            if x_kind != y_kind or not (x_words & y_words):
                continue
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
