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
_PERIOD_REPEAT_RATIO = 0.15
_PERIOD_MAX_NULL_PCT = 50.0
_METRIC_ALT_RATIO = 0.35
_QTY_NAME = re.compile(r"quantit|volume|units|qty", re.I)
_PRICE_NAME = re.compile(r"price|asp|rate per|unit cost", re.I)
# A conversion/multiplier rate (FX rate, tax rate, discount rate, ...): not a
# measure to sum across rows, even though it's numeric like one.
_RATE_NAME = re.compile(r"\brate\b|fx[_ ]?rate|exchange[_ ]?rate", re.I)

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
        elif dtype == "numeric" and _RATE_NAME.search(name):
            role = "dimension"  # a conversion rate, not a value to sum
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

    period_repeats = {
        c: distinct_counts[c] <= max(3, len(df) * _PERIOD_REPEAT_RATIO)
        for c, r in first_pass.items() if r == "period"
    }

    metric_winner = winner("metric_label")
    id_winner = winner("id")

    metric_alternatives: list[str] = []
    if metric_winner:
        win_score = _identity_score(df, metric_winner, period_col)
        for col, role in first_pass.items():
            if col == metric_winner or role not in ("metric_label", "dimension"):
                continue
            if not 1 < distinct_counts[col] <= max(3, len(df) * 0.5):
                continue
            if _identity_score(df, col, period_col) >= win_score * _METRIC_ALT_RATIO:
                metric_alternatives.append(str(col))

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

    # Two measure columns that move almost in lockstep are usually the same
    # underlying figure reported twice (e.g. amount_local vs amount_usd on a
    # file where most rows are already USD) — proposing both as separate
    # metrics would double the KPI count with near-duplicates. Keep the first
    # (stable column order) as the representative; the rest note who they
    # duplicate so suggest_mapping can leave them out of value_cols.
    # Prefer a USD-named column as the survivor of a redundant pair — it's the
    # consolidated reporting currency in every FX-aware export we've seen.
    def _rate_rank(c):
        return 0 if re.search(r"(^|[\s_])usd([\s_]|$)", c, re.I) else 1
    measure_cols = sorted((c for c, r in final.items() if r == "measure"), key=_rate_rank)
    redundant_with: dict[str, str] = {}
    for i, a in enumerate(measure_cols):
        if a in redundant_with:
            continue
        for b in measure_cols[i + 1:]:
            if b in redundant_with:
                continue
            try:
                corr = pd.to_numeric(df[a], errors="coerce").corr(pd.to_numeric(df[b], errors="coerce"))
            except Exception:  # noqa: BLE001 - correlation is a nice-to-have, never fatal
                corr = None
            if corr is not None and corr >= 0.995:
                redundant_with[b] = a

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
                "redundant_with": redundant_with.get(str(col)),
                "period_repeats": period_repeats.get(col),
            }
        )

    return {
        "columns": profiles,
        "layout_guess": layout_guess,
        "wide_period_cols": [str(c) for c in wide_period_cols],
        "row_count": int(len(df)),
        "metric_alternatives": metric_alternatives,
    }


def suggest_mapping(profile: dict, sheet_period: str | None = None) -> dict:
    """Build a best-effort MappingSpec dict from profile_columns() output, plus
    a `warnings` list naming anything uncertain enough that the user should
    look before confirming — a low-confidence guess is still a starting point
    to correct, never a dead end. This is what /ingest/{id}/schema returns
    alongside the raw column profile, and what the mapping screen pre-fills
    instead of the user (or a hand-rolled frontend default) having to build a
    mapping from an empty form.

    Never raises: a file this couldn't make sense of still gets *a* mapping —
    layout stays "long" with nothing selected, and a warning says why — so the
    caller always has something to render and edit rather than a hard error.
    """
    roles = {c["column_name"]: c["guessed_role"] for c in profile["columns"]}
    distinct = {c["column_name"]: c["distinct_count"] for c in profile["columns"]}
    layout = profile["layout_guess"]
    warnings: list[str] = []

    def cols(role: str) -> list[str]:
        return [c for c, r in roles.items() if r == role]

    if layout == "wide":
        period_cols = profile["wide_period_cols"]
        metric_candidates = cols("metric_label")
        id_candidates = cols("id")
        mapping = {
            "layout": "wide",
            "wide_period_cols": period_cols,
            "wide_metric_col": metric_candidates[0] if metric_candidates else None,
            "wide_value_label": None if metric_candidates else "Value",
            "id_col": id_candidates[0] if id_candidates else None,
            "dimension_cols": cols("dimension"),
        }
        if not period_cols:
            warnings.append(
                "No columns look like period labels (Jan-25, 2025-03, …) — "
                "the file may need a different header row, or this may not "
                "be a wide layout. Pick the period columns manually below."
            )
        if not metric_candidates:
            warnings.append(
                "No column looks like a metric name — every row will be "
                "treated as the same metric. Choose the column that "
                "identifies what's being measured (e.g. Account, Channel)."
            )
        elif len(metric_candidates) > 1:
            warnings.append(
                f"Multiple columns could name the metric ({', '.join(metric_candidates)}); "
                f"picked \"{metric_candidates[0]}\" by how identity-like it looks — check this is right."
            )
    else:
        period_candidates = cols("period")
        metric_candidates = cols("metric_label")
        redundant = {c["column_name"]: c["redundant_with"] for c in profile["columns"] if c.get("redundant_with")}
        all_measures = cols("measure")
        measure_candidates = [c for c in all_measures if c not in redundant]
        dropped_dupes = [c for c in all_measures if c in redundant]
        id_candidates = cols("id")
        repeats = {c["column_name"]: c.get("period_repeats") for c in profile["columns"]}
        null_pct = {c["column_name"]: c.get("null_pct") or 0 for c in profile["columns"]}
        repeating = [c for c in period_candidates
                     if repeats.get(c) and null_pct.get(c, 0) <= _PERIOD_MAX_NULL_PCT]
        sparse = [c for c in period_candidates
                  if repeats.get(c) and null_pct.get(c, 0) > _PERIOD_MAX_NULL_PCT]
        transactional = [c for c in period_candidates if not repeats.get(c)]
        snapshot = bool(sheet_period) and bool(period_candidates) and not repeating
        preferred_period = (repeating or sparse or period_candidates or [None])[0]
        mapping = {
            "layout": "long",
            "period_col": None if snapshot else preferred_period,
            "period_literal": sheet_period if snapshot else None,
            "metric_col": metric_candidates[0] if metric_candidates else None,
            "budget_col": (cols("budget") or [None])[0],
            "id_col": id_candidates[0] if id_candidates else None,
            "dimension_cols": cols("dimension") + dropped_dupes + (period_candidates if snapshot else []),
        }
        if snapshot:
            warnings.append(
                f"This sheet reports a single as-of date ({sheet_period}); "
                f"{', '.join(period_candidates)} look like per-row transaction dates, so every row "
                f"is filed under {sheet_period} and those columns are kept as dimensions."
            )
        elif period_candidates and not repeating:
            reasons = []
            if transactional:
                reasons.append(f"{', '.join(transactional)} holds a different date on almost every row")
            if sparse:
                reasons.append(
                    f"{', '.join(sparse)} is empty on most rows "
                    f"({', '.join(f'{null_pct[c]:.0f}%' for c in sparse)})"
                )
            warnings.append(
                f"No column behaves like a reporting period: {'; '.join(reasons)}. This looks like a "
                f"reference/roster list (one row per record) rather than a time series — such a file has "
                f"no month-by-month history to track. Check the period column before confirming."
            )
        if dropped_dupes:
            warnings.append(
                f"{', '.join(dropped_dupes)} moved almost identically to "
                f"{', '.join(sorted({redundant[c] for c in dropped_dupes}))} — treated as the same "
                f"figure reported twice, so only one was kept as a value column."
            )
        if len(measure_candidates) > 1:
            mapping["value_cols"] = measure_candidates
            warnings.append(
                f"{len(measure_candidates)} numeric columns look like measures "
                f"({', '.join(measure_candidates)}) — each will become its own "
                f"metric crossed with {mapping['metric_col'] or 'the metric column'}."
            )
        else:
            mapping["value_col"] = measure_candidates[0] if measure_candidates else None
        if not period_candidates:
            warnings.append(
                "No column looks like a period/date — pick the one that "
                "identifies which month or quarter each row belongs to."
            )
        if not measure_candidates:
            warnings.append(
                "No numeric column looks like the value to track — pick the "
                "column with the actual/spend/count you want as the metric's value."
            )
        if len(metric_candidates) > 1:
            warnings.append(
                f"Multiple columns could name the metric ({', '.join(metric_candidates)}); "
                f"picked \"{metric_candidates[0]}\" by how identity-like it looks — check this is right."
            )

    metric_col = mapping.get("metric_col") or mapping.get("wide_metric_col")
    alternatives = [c for c in profile.get("metric_alternatives") or [] if c != metric_col]
    if metric_col and alternatives:
        warnings.append(
            f"Rows could be grouped by \"{metric_col}\" or by {', '.join(alternatives)} — these are "
            f"different reports, not different spellings of the same one, and nothing in the file's "
            f"structure says which you want. Confirm \"{metric_col}\" is the breakdown you're after."
        )

    # A metric-name column with very few distinct values relative to the
    # row count under-detects: it's probably an attribute, not the metric
    # identity, even though it won a name/cardinality tie-break upstream.
    if metric_col and distinct.get(metric_col, 0) <= 1:
        warnings.append(
            f"\"{metric_col}\" has only {distinct.get(metric_col, 0)} distinct value(s) — "
            f"that would collapse everything into a single metric. Double-check this is the right column."
        )

    confidence = "low" if warnings else "high"
    return {"mapping": mapping, "warnings": warnings, "confidence": confidence}
