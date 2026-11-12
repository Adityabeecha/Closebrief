"""Numeric-faithfulness guard (PRD Section 6.4 / 9.2).

Extracts every number from a generated narrative and verifies each one traces
to a value that was *provided* to the model — either a computed fact or a number
appearing in a retrieved context document — to the precision it is written in
("$102K" may stand for $102,074, "12%" may not stand for 12.6%). Rise/fall
wording next to a figure ("fell 12%", "3.1% ahead of plan", "−$370K") must also
agree with the sign of the computed change it cites. This is
the enforcement point for the project's core rule: the LLM never computes
numbers, and a narrative containing an unverifiable figure must never silently
pass. Invented KPI figures (e.g. "we lost 47 accounts") still fail; bare
durations and dates ("12-month trend", "March 1") are not treated as KPI
figures.
"""

import math
import re

from app.currency import symbol
from app.generation.prompts import percent_kpi_points
from app.schemas import ComputedFact, ContextSnippet

_CURRENCY_SYMBOLS = "$\u20ac\u00a3\u20b9\u00a5"


def _has_currency(raw: str) -> bool:
    return any(symbol in raw for symbol in _CURRENCY_SYMBOLS)


_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])([+\-\u2212]?[$\u20ac\u00a3\u20b9\u00a5]?\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(million|billion|thousand|[mbk](?![A-Za-z]))?\s*(%|percent\b|per\s+cent\b)?",
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
# figure ("12-month trend", "3 quarters"). Streak phrasing puts an adjective
# in between ("4 consecutive months") — that count is ours, not the model's.
_DURATION_AFTER = re.compile(
    r"^\s*-?\s*(?:(?:consecutive|straight|successive|sequential|trailing|full)\s+)?"
    r"(month|months|day|days|week|weeks|quarter|quarters|year|years|mo|yr)\b",
    re.IGNORECASE,
)
# A bare integer immediately preceded by a month name is a calendar day
# ("March 1, 2025").
_MONTH_BEFORE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october|"
    r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s*$",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"(?<![\w$\u20ac\u00a3\u20b9\u00a5])\d{4}-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?(?![\d%])")


class _Token(float):
    """A number read from prose. `half_unit` is half of its last written digit
    (scaled by any K/M/B suffix): the most rounding the writer could have done.
    "$4.2M" -> 50,000; "5.64%" -> 0.005; "48,210" -> 0.5."""
    half_unit: float = 0.0
    is_pct: bool = False


class _CompactAmount(_Token):
    """A whole-$K token (kept as a name for callers that check for it)."""


def _token(value: float, half_unit: float, cls: type = _Token, is_pct: bool = False) -> _Token:
    t = cls(value)
    t.half_unit = half_unit
    t.is_pct = is_pct
    return t


def _normalize(raw: str, suffix: str, percent: str) -> tuple[float, bool] | None:
    suffix = suffix or ""
    percent = percent or ""
    cleaned = raw.translate(str.maketrans("", "", _CURRENCY_SYMBOLS)).replace(",", "").replace("\u2212", "-")
    try:
        base = float(cleaned)
    except ValueError:
        return None
    is_percent = bool(percent)
    multiplier = 1.0 if is_percent else _MULTIPLIERS.get(suffix.lower(), 1.0)
    decimals = len(cleaned.split(".", 1)[1]) if "." in cleaned else 0
    half = 0.5 * (10 ** -decimals) * multiplier
    whole_k = not is_percent and _has_currency(raw) and suffix.lower() in ("k", "thousand") and "." not in cleaned
    return _token(base * multiplier, half, _CompactAmount if whole_k else _Token, is_percent), is_percent


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


def _is_date_or_duration(match: re.Match, text: str, raw: str, suffix: str, percent: str,
                         unit: str | None = None) -> bool:
    """True when the matched token is a bare integer used as a duration or a
    calendar day, rather than a KPI figure. For a KPI measured in days (DSO),
    "47 days" IS the figure and must be checked."""
    if percent or suffix or _has_currency(raw) or "." in raw:
        return False
    if (unit or "").lower() == "days" and re.match(r"\s*days?\b", text[match.end():match.end() + 8], re.I):
        return False
    digits = raw.replace(",", "").lstrip("+-\u2212")
    if not digits.isdigit():
        return False
    if any(date.start() <= match.start(1) and match.end(1) <= date.end()
           for date in _ISO_DATE.finditer(text)):
        return True
    after = text[match.end() : match.end() + 32]
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
    if (fact.unit or "").lower() == "days":
        # A days KPI's changes are quoted in days ("up 3 days").
        magnitudes.update(p for p in percent_kpi_points(fact).values() if p is not None)
    if (fact.unit or "").lower() in ("%", "percent"):
        percents.add(fact.value)
        if fact.prior_value is not None:
            percents.add(fact.prior_value)
        # Its changes are quoted in points ("down 2.0 points"), which read as plain numbers.
        magnitudes.update(p for p in percent_kpi_points(fact).values() if p is not None)

    return magnitudes, percents


def _context_values(context: list[ContextSnippet] | None) -> tuple[set[float], set[float]]:
    """Keep context magnitudes and percentages in the same buckets as facts."""
    magnitudes: set[float] = set()
    percents: set[float] = set()
    for snippet in context or []:
        for value, is_percent in extract_numbers(snippet.body):
            (percents if is_percent else magnitudes).add(abs(value))
    return magnitudes, percents


def _excluded_tokens(fact: ComputedFact) -> set[float]:
    """Numbers that legitimately appear in prose without being a computed
    fact — the period's year/month — so they aren't flagged as invented.
    """
    excluded = set()
    try:
        year_str, month_str = fact.period.split("-")
        year = int(year_str)
        excluded.update({float(year), float(year - 1)})
    except (ValueError, IndexError):
        pass
    return excluded


def _matches_any(candidate: float, allowed: set[float], tolerance_pct: float) -> bool:
    """True when `candidate` is one of `allowed` as written. A number read from
    prose matches only within its own rounding (half its last digit). Amounts
    are also held to 2.5% of the real value, so a coarse "$1M" can't stand for
    $1.5M. A plain float (older callers) falls back to `tolerance_pct`."""
    half = getattr(candidate, "half_unit", None)
    is_pct = getattr(candidate, "is_pct", False)
    for value in allowed:
        if value is None:
            continue
        # The UI's whole-thousand currency form ("$102K") rounds half-up to the
        # nearest $1K, so it matches exactly that rounding of a $1K–$1M value.
        if isinstance(candidate, _CompactAmount) and 1e3 <= abs(value) < 1e6:
            if abs(candidate) == math.floor(abs(value) / 1e3 + 0.5) * 1e3:
                return True
            continue
        if half is None:
            tolerance = max(0.5, abs(value) * tolerance_pct)
        elif is_pct:
            tolerance = half * (1 + 1e-9) + 1e-9
        else:
            tolerance = min(half, max(0.5, abs(value) * 0.025)) * (1 + 1e-9) + 1e-9
        if abs(abs(candidate) - abs(value)) <= tolerance:
            return True
    return False


# ---- Direction: "rose", "fell", "ahead of", "below", or an explicit +/− sign ----
_UP_WORDS = {"rose", "rise", "rises", "risen", "rising", "increase", "increased", "increases",
             "increasing", "grew", "grow", "grows", "growth", "grown", "gain", "gained", "gains",
             "climbed", "climb", "climbs", "jumped", "jump", "jumps", "surged", "surge", "up",
             "higher", "ahead", "above", "exceeded", "exceeds", "beat", "beats"}
_DOWN_WORDS = {"fell", "fall", "falls", "fallen", "falling", "decrease", "decreased", "decreases",
               "decreasing", "decline", "declined", "declines", "declining", "drop", "dropped",
               "drops", "down", "lower", "behind", "below", "missed", "lost", "shrank", "shrunk",
               "slipped", "slid", "contracted", "short"}
_AFTER_UP = {"higher", "ahead", "above", "up", "more"}
_AFTER_DOWN = {"lower", "behind", "below", "down", "less"}
_PLAN_WORDS = {"plan", "budget", "target", "forecast"}
_CLAUSE_BREAK = re.compile(r"[.;:!?,]|\b(?:and|but|while|whereas|although|though)\b", re.I)
_WORD = re.compile(r"[A-Za-z]+")


def _claimed_direction(text: str, match: re.Match) -> tuple[int, bool]:
    """(+1 up / -1 down / 0 none, follows "to") for the number at `match`.
    An explicit sign wins; otherwise the nearest rise/fall word earlier in the
    same clause (up to six words back); otherwise a comparative right after
    ("3.1% ahead of plan", "12% higher")."""
    raw = match.group(1)
    before = text[:match.start()]
    breaks = [m.end() for m in _CLAUSE_BREAK.finditer(before)]
    words = [w.lower() for w in _WORD.findall(before[breaks[-1] if breaks else 0:])][-6:]
    after_to = bool(words) and words[-1] == "to"
    if raw[:1] == "+":
        return 1, after_to
    if raw[:1] in ("-", "\u2212"):
        return -1, after_to
    for w in reversed(words):
        if w in _UP_WORDS:
            return 1, after_to
        if w in _DOWN_WORDS:
            return -1, after_to
    nxt = [w.lower() for w in _WORD.findall(text[match.end():match.end() + 40])][:3]
    for i, w in enumerate(nxt):
        if w in _AFTER_UP:
            return 1, False
        if w in _AFTER_DOWN:
            return -1, False
        if w in ("over", "under") and i + 1 < len(nxt) and nxt[i + 1] in _PLAN_WORDS:
            return (1 if w == "over" else -1), False
    return 0, after_to


def _sign(v: float | None) -> int:
    return 0 if v is None or v == 0 else (1 if v > 0 else -1)


def direction_issue(text: str, match: re.Match, value: float, is_percent: bool,
                    fact: ComputedFact) -> str | None:
    """A sentence that says a figure rose when it fell (or the reverse). Only the
    computed changes are judged (vs last period, vs last year, vs plan, bridge
    effects), plus a level reached "to" ("rose to $4.2M") against the change
    since last period. Returns a plain-English problem, or None."""
    claim, after_to = _claimed_direction(text, match)
    if claim == 0:
        return None
    d = fact.deltas
    if is_percent:
        changes = [d.mom_pct, d.yoy_pct, d.budget_var_pct]
        levels = [fact.value, fact.prior_value] if (fact.unit or "").lower() in ("%", "percent") else []
    else:
        changes = [d.budget_var_abs, *((fact.variance_bridge or {}).values())]
        levels = [fact.value, fact.prior_value]
    signs = [_sign(v) for v in changes if v is not None and _matches_any(value, {v}, 0.0)]
    if after_to and any(v is not None and _matches_any(value, {v}, 0.0) for v in levels):
        signs.append(_sign(d.mom_pct))
    signs = [s for s in signs if s != 0]
    if not signs or claim in signs:
        return None
    return "rose" if claim < 0 else "fell"   # what the figure actually did


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
        if not any(abs(implied - value) <= max(0.5, abs(value) * 0.10)
                   for value in percents):
            unverified.append(implied)
    return unverified


def check_faithfulness(
    narrative: str,
    fact: ComputedFact,
    context: list[ContextSnippet] | None = None,
    tolerance_pct: float = 0.02,
) -> tuple[bool, list[float]]:
    """Returns (passed, unverified_numbers). `passed` is True iff every KPI-like
    number in the narrative maps, to its written precision, to a computed fact
    or a number present in the provided context, and any rise/fall wording on a
    computed change agrees with its sign. (`tolerance_pct` only applies to plain
    floats from older callers; numbers read from text use their precision.)
    """
    magnitudes, percents = _allowed_values(fact)
    excluded = _excluded_tokens(fact)
    context_magnitudes, context_percents = _context_values(context)

    unverified: list[float] = []
    for match in _NUMBER_RE.finditer(narrative):
        raw, suffix, percent = match.group(1), match.group(2), match.group(3)
        norm = _normalize(raw, suffix, percent)
        if norm is None:
            continue
        value, is_percent = norm

        if not percent and not suffix and not _has_currency(raw) and "." not in raw and value in excluded:
            continue
        if _is_date_or_duration(match, narrative, raw, suffix, percent, fact.unit):
            continue

        allowed = percents if is_percent else magnitudes
        if _matches_any(value, allowed, tolerance_pct):
            if direction_issue(narrative, match, value, is_percent, fact):
                unverified.append(value)
            continue
        context_vals = context_percents if is_percent else context_magnitudes
        if _matches_any(value, context_vals, tolerance_pct):
            continue
        unverified.append(value)

    unverified.extend(_check_multiplier_claims(narrative, percents))

    return (len(unverified) == 0, unverified)


def faithfulness_messages(narrative: str, fact: ComputedFact,
                          context: list[ContextSnippet] | None = None) -> list[str]:
    """Human explanations only; check_faithfulness's numeric contract is unchanged."""
    passed, unverified = check_faithfulness(narrative, fact, context)
    if passed:
        return []
    def amount(value, percent=False):
        if percent:
            return f"{value:g}%"
        if (fact.unit or '').lower() in ('count', 'customers', 'logos'):
            return f"{value:,.0f}"
        sign, mark = ('-' if value < 0 else ''), symbol(fact.currency)
        value = abs(value)
        if value >= 1e6:
            return f"{sign}{mark}{value / 1e6:.2f}M"
        if value >= 1e3:
            return f"{sign}{mark}{value / 1e3:g}K"
        return f"{sign}{mark}{value:g}"
    magnitudes, percents = _allowed_values(fact)
    ctx_magnitudes, ctx_percents = _context_values(context)
    excluded = _excluded_tokens(fact)
    messages = []
    for match in _NUMBER_RE.finditer(narrative):
        norm = _normalize(*match.groups())
        if not norm:
            continue
        value, percent = norm
        raw, suffix, percent_token = match.groups()
        if not percent_token and not suffix and not _has_currency(raw) and '.' not in raw and value in excluded:
            continue
        if _is_date_or_duration(match, narrative, raw, suffix, percent_token, fact.unit):
            continue
        token = match.group().strip()
        if _matches_any(value, percents if percent else magnitudes, .02):
            went = direction_issue(narrative, match, value, percent, fact)
            if went:
                messages.append(f"The text calls {token} a {'fall' if went == 'rose' else 'rise'}, "
                                f"but the computed figure {went}")
            continue
        if _matches_any(value, ctx_percents if percent else ctx_magnitudes, .02):
            continue
        actual = amount(fact.value, (fact.unit or '').lower() in ('%', 'percent'))
        # Avoid implying a percent mismatch is the current metric's currency value.
        suffix = f" (the actual is {actual})" if not norm[1] or (fact.unit or '').lower() in ('%', 'percent') else ''
        messages.append(f"{token} doesn't match any computed figure{suffix}")
    if not messages:
        messages.append("The claimed change doesn't match the computed figures. Review the change against the source data.")
    return list(dict.fromkeys(messages))
