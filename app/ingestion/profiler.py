"""Column profiling + role guessing (Addendum v1.1 Section 1.4).

Profiles an arbitrary DataFrame and proposes a role for each column:
period | metric_label | measure | budget | dimension | ignore.
Also detects the wide layout (many columns whose *names* are periods).
Guesses are proposals — the user confirms or overrides in the mapping step.
No column name is ever required or hardcoded.
"""

import re

import pandas as pd

# Column-name patterns (case-insensitive)
_BUDGET_NAME = re.compile(r"budget|plan|forecast|target|\bbud\b|\bfcst\b", re.I)
_METRIC_NAME = re.compile(r"metric|account|line ?item|kpi|\bgl\b", re.I)
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


def profile_columns(df: pd.DataFrame) -> dict:
    """Returns {"columns": [profile per column], "layout_guess": "long"|"wide",
    "wide_period_cols": [...]} — the schema-detection payload."""
    wide_period_cols = [c for c in df.columns if is_period_name(c)]
    layout_guess = "wide" if len(wide_period_cols) >= 3 else "long"

    profiles = []
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

        distinct = int(non_null.nunique())
        name = str(col)

        if col in wide_period_cols and layout_guess == "wide":
            role = "measure"  # each wide period column carries values
        elif dtype == "date" or (_PERIOD_NAME.search(name) and dtype != "numeric"):
            role = "period"
        elif dtype == "numeric" and _BUDGET_NAME.search(name):
            role = "budget"
        elif dtype == "numeric":
            role = "measure"
        elif dtype == "text" and _METRIC_NAME.search(name):
            role = "metric_label"
        elif dtype == "text" and _DIMENSION_NAME.search(name):
            role = "dimension"
        elif dtype == "text" and 1 <= distinct <= max(3, int(len(df) * 0.5)):
            # low-to-mid cardinality text with no better name signal: could be
            # a metric label; propose it if nothing else claims the role
            role = "metric_label" if not _DIMENSION_NAME.search(name) else "dimension"
        else:
            role = "ignore"

        profiles.append(
            {
                "column_name": name,
                "dtype": dtype,
                "sample_values": [str(v) for v in non_null.head(5).tolist()],
                "distinct_count": distinct,
                "null_pct": round(float(series.isna().mean()) * 100, 1),
                "guessed_role": role,
            }
        )

    return {
        "columns": profiles,
        "layout_guess": layout_guess,
        "wide_period_cols": [str(c) for c in wide_period_cols],
    }
