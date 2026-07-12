"""Sentence-level attribution (v3.0 Narrative Drill-Down).

For a generated narrative, attribute each sentence to the exact computed facts
and context documents that ground it — deterministically, reusing the guard's
number extraction and matching. No LLM call: the same numbers the faithfulness
guard already parses are mapped to the specific fact field they came from, and
causes are traced to context snippets by token overlap.

This is the audit trail behind every sentence, and the single mechanism shared
with the faithfulness gate (app.generation.guard).
"""

from __future__ import annotations

import re

from app.generation.guard import (
    _NUMBER_RE,
    _excluded_tokens,
    _is_date_or_duration,
    _matches_any,
    _normalize,
    extract_numbers,
)
from app.schemas import ComputedFact, ContextSnippet

# Sentence boundary: end punctuation followed by whitespace. Decimals ("5.33M")
# and figures have no space after the dot, so they are not split.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_STOP = {
    "this", "that", "with", "from", "into", "than", "then", "them", "were", "was",
    "the", "and", "for", "came", "come", "rose", "fell", "held", "flat", "plan",
    "have", "has", "over", "under", "month", "period", "versus", "which", "while",
    "aligns", "note", "warrants", "closer", "look", "move", "moved", "movement",
}


def _labeled_fact_values(fact: ComputedFact) -> list[tuple[str, float, bool]]:
    """(human label, value, is_percent) for every field a narrative may cite."""
    out: list[tuple[str, float, bool]] = [("current value", fact.value, False)]
    if fact.prior_value is not None:
        out.append(("prior-period value", fact.prior_value, False))
    d = fact.deltas
    if d.budget_var_abs is not None:
        out.append(("budget variance (abs)", d.budget_var_abs, False))
    if d.mom_pct is not None:
        out.append(("month-over-month %", d.mom_pct, True))
    if d.yoy_pct is not None:
        out.append(("year-over-year %", d.yoy_pct, True))
    if d.budget_var_pct is not None:
        out.append(("budget variance %", d.budget_var_pct, True))
    if fact.variance_bridge:
        for comp, val in fact.variance_bridge.items():
            if val is not None:
                out.append((f"{comp} (variance bridge)", float(val), False))
    return out


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


def _split_sentences(narrative: str) -> list[tuple[int, str, int, int]]:
    """(idx, text, start, end) char spans over the original string, so the UI can
    render the exact same sentences the attribution was computed on."""
    out, cursor = [], 0
    for i, piece in enumerate(_SENTENCE_SPLIT.split(narrative.strip())):
        if not piece:
            continue
        start = narrative.find(piece, cursor)
        if start < 0:
            start = cursor
        end = start + len(piece)
        cursor = end
        out.append((i, piece, start, end))
    return out


def attribute(narrative: str, fact: ComputedFact,
              context: list[ContextSnippet] | None = None,
              tolerance_pct: float = 0.02) -> dict:
    """Return per-sentence grounding plus any unverified numbers.

    Each sentence gets: the fact fields it cites (with values), the context doc
    ids it draws on, and a `kind` (verified | numeric-unverified | context |
    general). `unverified` mirrors the faithfulness guard's finding.
    """
    labeled = _labeled_fact_values(fact)
    percents = [(lbl, v) for (lbl, v, isp) in labeled if isp]
    magnitudes = [(lbl, v) for (lbl, v, isp) in labeled if not isp]
    ctx = context or []
    ctx_tokens = [(c, _tokens((c.title or "") + " " + (c.body or ""))) for c in ctx]
    # Absolute magnitudes present in each context body — numbers the model may cite.
    ctx_numbers = {abs(v) for c in ctx for v, _ in extract_numbers(c.body or "")}
    excluded = _excluded_tokens(fact)   # period year/month digits aren't KPI figures

    sentences, unverified_all = [], []
    for idx, text, start, end in _split_sentences(narrative):
        cited_facts, ctx_ids, unverified = [], [], []
        # Numeric attribution: map each figure to the specific fact field.
        for m in _NUMBER_RE.finditer(text):
            raw, suffix, percent = m.group(1), m.group(2), m.group(3)
            norm = _normalize(raw, suffix, percent)
            if norm is None:
                continue
            value, is_percent = norm
            if value in excluded or -value in excluded:
                continue
            if _is_date_or_duration(m, text, raw, suffix, percent):
                continue
            pool = percents if is_percent else magnitudes
            hit = next((lbl for (lbl, v) in pool
                        if _matches_any(value, {v}, tolerance_pct)), None)
            if hit is not None:
                cited_facts.append({"field": hit, "value": value, "is_percent": is_percent})
            elif not _matches_any(value, ctx_numbers, tolerance_pct):
                # Not a fact and not a number present in context → unverified.
                unverified.append(value)
        # Causal attribution: which context docs this sentence draws on.
        s_tokens = _tokens(text)
        for c, c_tokens in ctx_tokens:
            title = (c.title or "").lower()
            if (title and title in text.lower()) or len(s_tokens & c_tokens) >= 2:
                ctx_ids.append(c.id)

        kind = ("numeric-unverified" if unverified
                else "verified" if cited_facts
                else "context" if ctx_ids
                else "general")
        sentences.append({
            "idx": idx, "text": text, "start": start, "end": end,
            "facts": cited_facts, "context_ids": ctx_ids, "kind": kind,
        })
        unverified_all.extend(unverified)

    return {
        "sentences": sentences,
        "unverified": unverified_all,
        "faithful": len(unverified_all) == 0,
    }
