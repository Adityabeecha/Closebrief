"""Numeric-faithfulness guard (PRD Section 6.4 / 9.2).

Extracts every number from a generated narrative and verifies each one traces,
within tolerance, to a value that was *provided* to the model — either a
computed fact or a number appearing in a retrieved context document. This is
the enforcement point for the project's core rule: the LLM never computes
numbers, and a narrative containing an unverifiable figure must never silently
pass. Invented KPI figures (e.g. "we lost 47 accounts") still fail; bare
durations and dates ("12-month trend", "March 1") are not treated as KPI
figures.
"""

import re

from app.schemas import ComputedFact, ContextSnippet

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(-?\$?\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(million|billion|thousand|[mbk](?![A-Za-z]))?\s*(%)?",
    re.IGNORECASE,
)

_MULTIPLIERS = {
    "million": 1e6,
    "m": 1e6,
    "billion": 1e9,
    "b": 1e9,
    "thousand": 1e3,
    "k": 1e3,
}

# A bare integer immediately followed by one of these is a duration, not a KPI
# figure ("12-month trend", "3 quarters").
_DURATION_AFTER = re.compile(
    r"^\s*-?\s*(month|months|day|days|week|weeks|quarter|quarters|year|years|mo|yr)\b",
    re.IGNORECASE,
)
# A bare integer immediately preceded by a month name is a calendar day
# ("March 1, 2025").
_MONTH_BEFORE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october|"
    r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s*$",
    re.IGNORECASE,
)


def _normalize(raw: str, suffix: str, percent: str) -> tuple[float, bool] | None:
    suffix = suffix or ""
    percent = percent or ""
    cleaned = raw.replace("$", "").replace(",", "")
    try:
        base = float(cleaned)
    except ValueError:
        return None
    is_percent = bool(percent)
    multiplier = 1.0 if is_percent else _MULTIPLIERS.get(suffix.lower(), 1.0)
    return base * multiplier, is_percent


def extract_numbers(text: str) -> list[tuple[float, bool]]:
    """Return (value, is_percent) for every numeric token in `text`,
    with $ / comma / M-B-K suffixes normalized to their absolute magnitude.
    """
    numbers: list[tuple[float, bool]] = []
    for raw, suffix, percent in _NUMBER_RE.findall(text):
        norm = _normalize(raw, suffix, percent)
        if norm is not None:
            numbers.append(norm)
    return numbers


def _is_date_or_duration(match: re.Match, text: str, raw: str, suffix: str, percent: str) -> bool:
    """True when the matched token is a bare integer used as a duration or a
    calendar day, rather than a KPI figure."""
    if percent or suffix or "$" in raw or "." in raw:
        return False
    digits = raw.replace(",", "").lstrip("-")
    if not digits.isdigit():
        return False
    after = text[match.end() : match.end() + 12]
    before = text[max(0, match.start() - 12) : match.start()]
    return bool(_DURATION_AFTER.match(after) or _MONTH_BEFORE.search(before))


def _allowed_values(fact: ComputedFact) -> tuple[set[float], set[float]]:
    """Split computed facts into (magnitude_values, percent_values) buckets,
    since a percent figure like "12%" should only be compared against percent
    facts, not against dollar magnitudes.
    """
    magnitudes = {fact.value}
    if fact.prior_value is not None:
        magnitudes.add(fact.prior_value)
    if fact.deltas.budget_var_abs is not None:
        magnitudes.add(fact.deltas.budget_var_abs)
    if fact.variance_bridge:
        magnitudes.update(fact.variance_bridge.values())

    percents = set()
    for p in (fact.deltas.mom_pct, fact.deltas.yoy_pct, fact.deltas.budget_var_pct):
        if p is not None:
            percents.add(p)
    # A percent-unit metric's own value IS a percent (e.g. Gross Margin 60% or a
    # derived %-KPI), so allow it to verify when cited with a "%" sign.
    if (fact.unit or "").lower() in ("%", "percent"):
        percents.add(fact.value)
        if fact.prior_value is not None:
            percents.add(fact.prior_value)

    return magnitudes, percents


def _context_values(context: list[ContextSnippet] | None) -> set[float]:
    """Absolute magnitudes of every number appearing in provided context bodies.
    These are grounding the model is allowed to cite (PRD Section 11)."""
    values: set[float] = set()
    for snippet in context or []:
        for value, _is_percent in extract_numbers(snippet.body):
            values.add(abs(value))
    return values


def _excluded_tokens(fact: ComputedFact) -> set[float]:
    """Numbers that legitimately appear in prose without being a computed
    fact — the period's year/month — so they aren't flagged as invented.
    """
    excluded = set()
    try:
        year_str, month_str = fact.period.split("-")
        year = int(year_str)
        excluded.update({float(year), float(year - 1), float(int(month_str))})
    except (ValueError, IndexError):
        pass
    return excluded


def _matches_any(candidate: float, allowed: set[float], tolerance_pct: float) -> bool:
    for value in allowed:
        tolerance = max(0.5, abs(value) * tolerance_pct)
        if abs(abs(candidate) - abs(value)) <= tolerance:
            return True
    return False


# Spelled-out multiplier claims ("revenue doubled") are numeric claims too.
# Each maps to the percent change it implies; the claim must match a percent
# fact within a loose tolerance or it counts as unverified.
_MULTIPLIER_WORDS = {
    "doubled": 100.0,
    "tripled": 200.0,
    "quadrupled": 300.0,
    "halved": -50.0,
    "twofold": 100.0,
    "threefold": 200.0,
}
_MULTIPLIER_RE = re.compile(
    r"\b(" + "|".join(_MULTIPLIER_WORDS) + r")\b", re.I
)


def _check_multiplier_claims(narrative: str, percents: set[float]) -> list[float]:
    """Return implied-percent values for multiplier words that don't match
    any percent fact (within 10% relative tolerance)."""
    unverified = []
    for word in _MULTIPLIER_RE.findall(narrative):
        implied = _MULTIPLIER_WORDS[word.lower()]
        if not _matches_any(implied, percents, tolerance_pct=0.10):
            unverified.append(implied)
    return unverified


def check_faithfulness(
    narrative: str,
    fact: ComputedFact,
    context: list[ContextSnippet] | None = None,
    tolerance_pct: float = 0.02,
) -> tuple[bool, list[float]]:
    """Returns (passed, unverified_numbers). `passed` is True iff every KPI-like
    number in the narrative maps, within tolerance, to a computed fact or a
    number present in the provided context.
    """
    magnitudes, percents = _allowed_values(fact)
    excluded = _excluded_tokens(fact)
    context_vals = _context_values(context)

    unverified: list[float] = []
    for match in _NUMBER_RE.finditer(narrative):
        raw, suffix, percent = match.group(1), match.group(2), match.group(3)
        norm = _normalize(raw, suffix, percent)
        if norm is None:
            continue
        value, is_percent = norm

        if value in excluded or -value in excluded:
            continue
        if _is_date_or_duration(match, narrative, raw, suffix, percent):
            continue

        allowed = percents if is_percent else magnitudes
        if _matches_any(value, allowed, tolerance_pct):
            continue
        if _matches_any(value, context_vals, tolerance_pct):
            continue
        unverified.append(value)

    unverified.extend(_check_multiplier_claims(narrative, percents))

    return (len(unverified) == 0, unverified)
