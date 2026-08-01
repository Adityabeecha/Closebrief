"""Column profiling + role guessing (Addendum v1.1 Section 1.4).

Profiles an arbitrary DataFrame and proposes a role for each column:
period | metric_label | measure | budget | id | dimension | ignore.
Also detects the wide layout (many columns whose *names* are periods).
Guesses are proposals — the user confirms or overrides in the mapping step.
No column name is ever required or hardcoded.

Role assignment is two-pass: pass 1 gives every column an independent
per-role SCORE (name pattern + shape signals); pass 2 breaks ties within each
contested role (there is often more than one numeric column, more than one
low-cardinality text column) using how identity-like a column is relative to
the others — see `_identity_score`. Only the highest scorer in a role is
proposed for that role; the rest fall back to their next-best fit rather than
silently losing to "whichever came first in the file".
"""

import re

import pandas as pd

# Column-name patterns (case-insensitive)
_BUDGET_NAME = re.compile(r"budget|plan|forecast|target|\bbud\b|\bfcst\b", re.I)
_ID_NAME = re.compile(r"[\s_]code\b|^code\b|[\s_]id\b|^id\b|\bsku\b", re.I)
_METRIC_NAME = re.compile(r"metric|account|line ?item|kpi|\bgl\b|channel|vendor|category", re.I)
_DIMENSION_NAME = re.compile(
    r"department|region|entity|cost ?cent(er|re)|product|segment|division|team", re.I
)
_PERIOD_NAME = re.compile(r"period|month|date|quarter|year|\bfy\b", re.I)
_QTY_NAME = re.compile(r"quantit|volume|units|qty", re.I)
_PRICE_NAME = re.compile(r"price|asp|rate per|unit cost", re.I)

# Period-shaped column NAMES for wide-layout detection: Jan-25, 2025-03,
# Q1 2025, FY24, Mar 2025, 01/2025 ...
_PERIOD_SHAPED = re.compile(
    r"^\s*("
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-_/.']*\d{2,4}"
    r"|\d{4}[\s\-_/.]?(0?[1-9]|1[0-2])"
    r"|(0?[1-9]|1[0-2])[\s\-_/.]\d{4}"
    r"|q[1-4][\s\-_/.']*\d{2,4}"
    r"|fy[\s\-_/.']*\d{2,4}"
    r")\s*$",
    re.I,
)


def _values_parse_as_dates(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).head(30)
    if sample.empty:
        return False
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    return parsed.notna().mean() >= 0.8


def _values_parse_as_numbers(series: pd.Series) -> float:
    """Fraction of non-null values that coerce to a number after cleaning
    currency symbols, commas, and (accounting) negatives."""
    sample = series.dropna().astype(str).head(50)
    if sample.empty:
        return 0.0
    cleaned = (
        sample.str.replace(r"[\$€£,\s]", "", regex=True)
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    )
    return pd.to_numeric(cleaned, errors="coerce").notna().mean()


def is_period_name(name: str) -> bool:
    return bool(_PERIOD_SHAPED.match(str(name)))


def _identity_score(df: pd.DataFrame, col, period_col: str | None) -> float:
    """How identity-like a text column is: close to 1.0 means each row is
    "one thing" (an account, a vendor, a channel) — the trait a true metric-
    name or id column has and an attribute/dimension column (currency,
    category, auto-renew) does not, regardless of raw cardinality.

    Scored as mean distinct-values-per-period ÷ mean rows-per-period when a
    period column is known (e.g. 33 vendors each appearing once a month scores
    near 1.0; a 2-valued flag repeated on every row scores near 0). Falls back
    to plain distinct/row-count when no period column is identified yet."""
    if period_col and period_col in df.columns:
        try:
            per_period_rows = df.groupby(period_col).size()
            per_period_distinct = df.groupby(period_col)[col].nunique()
            if len(per_period_rows) and per_period_rows.mean() > 0:
                return float((per_period_distinct / per_period_rows).mean())
        except Exception:  # noqa: BLE001 - fall through to the simpler ratio below
            pass
    n = len(df)
    return float(df[col].nunique()) / n if n else 0.0


def profile_columns(df: pd.DataFrame) -> dict:
    """Returns {"columns": [profile per column], "layout_guess": "long"|"wide",
    "wide_period_cols": [...]} — the schema-detection payload."""
    wide_period_cols = [c for c in df.columns if is_period_name(c)]
    layout_guess = "wide" if len(wide_period_cols) >= 3 else "long"

    dtypes: dict = {}
    distinct_counts: dict = {}
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        numeric_frac = _values_parse_as_numbers(series)
        is_dateish = _values_parse_as_dates(series) if numeric_frac < 0.8 else False
        if numeric_frac >= 0.8:
            dtype = "numeric"
        elif is_dateish:
            dtype = "date"
        elif non_null.empty:
            dtype = "mixed"
        else:
            dtype = "text"
        dtypes[col] = dtype
        distinct_counts[col] = int(non_null.nunique())

    # A single-column first pass, independent of any other column, matching
    # the previous behaviour — then a second pass resolves ties within
    # metric_label/measure/id by identity score so exactly one column wins
    # each contested role instead of "whichever came first".
    first_pass: dict = {}
    for col in df.columns:
        dtype = dtypes[col]
        distinct = distinct_counts[col]
        name = str(col)

        if col in wide_period_cols and layout_guess == "wide":
            role = "measure"  # each wide period column carries values
        elif dtype == "date" or (_PERIOD_NAME.search(name) and dtype != "numeric"):
            role = "period"
        elif dtype in ("numeric", "text") and _ID_NAME.search(name):
            role = "id"
        elif dtype == "numeric" and _BUDGET_NAME.search(name):
            role = "budget"
        elif dtype == "numeric" and _QTY_NAME.search(name):
            role = "quantity"
        elif dtype == "numeric" and _PRICE_NAME.search(name):
            role = "price"
        elif dtype == "numeric":
            role = "measure"
        elif dtype == "text" and _METRIC_NAME.search(name):
            role = "metric_label"
        elif dtype == "text" and _DIMENSION_NAME.search(name):
            role = "dimension"
        elif dtype == "text" and 1 <= distinct <= max(3, int(len(df) * 0.5)):
            role = "metric_label" if not _DIMENSION_NAME.search(name) else "dimension"
        elif dtype == "text" and layout_guess == "wide" and distinct >= 1:
            # In wide layout each row is typically one metric melted across
            # period columns, so the metric-label column is near-1:1 with row
            # count — the long-layout cardinality cap above would wrongly
            # exclude it (e.g. 10 channels out of 10-11 rows).
            role = "metric_label" if not _DIMENSION_NAME.search(name) else "dimension"
        else:
            role = "ignore"
        first_pass[col] = role

    # Best-guess period column, for the identity-score ratio above.
    period_col = next((c for c, r in first_pass.items() if r == "period"), None)

    def winner(role: str) -> str | None:
        candidates = [c for c, r in first_pass.items() if r == role]
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        scored = [(c, _identity_score(df, c, period_col)) for c in candidates]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[0][0]

    metric_winner = winner("metric_label")
    id_winner = winner("id")

    final: dict = {}
    for col, role in first_pass.items():
        if role == "metric_label" and col != metric_winner:
            # Runner-up metric_label candidates are almost always attributes
            # OF the metric (currency, category, contract type) — dimension
            # is the correct proposal, not silent disappearance into "ignore".
            final[col] = "dimension"
        elif role == "id" and col != id_winner:
            final[col] = "dimension"
        else:
            final[col] = role

    profiles = []
    for col in df.columns:
        non_null = df[col].dropna()
        profiles.append(
            {
                "column_name": str(col),
                "dtype": dtypes[col],
                "sample_values": [str(v) for v in non_null.head(5).tolist()],
                "distinct_count": distinct_counts[col],
                "null_pct": round(float(df[col].isna().mean()) * 100, 1),
                "guessed_role": final[col],
            }
        )

    return {
        "columns": profiles,
        "layout_guess": layout_guess,
        "wide_period_cols": [str(c) for c in wide_period_cols],
    }
