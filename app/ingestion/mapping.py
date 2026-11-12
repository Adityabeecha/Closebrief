"""Stages 3-4 — Apply a confirmed mapping and normalize to the canonical
long format (Addendum v1.1 Sections 1.5-1.7):

    period | metric | value | budget | quantity | price | dimensions

Handles both layouts. Wide (months as columns — the common real FP&A export)
is melted; long is renamed. Values are coerced with currency symbols, commas,
and accounting negatives ("(1,200)" -> -1200) stripped. Periods are parsed to
"YYYY-MM". No column name is hardcoded — everything comes from the mapping.
"""

import json
import math
import re
import sqlite3
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field


class MappingSpec(BaseModel):
    layout: str = Field(pattern="^(long|wide)$")
    # long layout
    period_col: Optional[str] = None
    period_literal: Optional[str] = None
    metric_col: Optional[str] = None
    value_col: Optional[str] = None
    # When multiple numeric columns are all measures (not one value column per
    # metric row), each is crossed with metric_col into its own metric — e.g.
    # 10 channels x [Spend, Clicks] -> "<channel> Spend", "<channel> Clicks".
    value_cols: list[str] = Field(default_factory=list)
    budget_col: Optional[str] = None
    quantity_col: Optional[str] = None
    price_col: Optional[str] = None
    budget_quantity_col: Optional[str] = None
    budget_price_col: Optional[str] = None
    dimension_cols: list[str] = Field(default_factory=list)
    # A stable secondary identifier alongside metric_col (e.g. a GL code next
    # to an account name). Two jobs: (1) a row whose id is blank while its
    # label is a roll-up/total is dropped rather than ingested as a metric;
    # (2) preferred over the label when joining a later upload's budget onto
    # existing metrics (a code doesn't drift the way "  Foo Bar" vs "Foo Bar"
    # or "Foo/Bar" vs "Foo Bar" does).
    id_col: Optional[str] = None
    # Explicitly retain source labels that the summary-row heuristic would drop.
    keep_labels: list[str] = Field(default_factory=list)
    # wide layout
    wide_period_cols: list[str] = Field(default_factory=list)
    wide_value_label: Optional[str] = None
    wide_metric_col: Optional[str] = None  # optional: metric names in a column
    # Optional budget columns parallel to wide_period_cols, same order, so a
    # wide layout can carry budget instead of nulling the variance story.
    wide_budget_cols: list[str] = Field(default_factory=list)


class MappingError(Exception):
    """Validation failure. Message names the specific problem column."""


_QUARTER_LABEL = re.compile(r"^(?:q[1-4][\s\-_/.']*\d{2,4}|\d{2,4}[\s\-_/.']*q[1-4])$", re.I)
_YEAR_LABEL = re.compile(
    r"^(?:(?:fy|fiscal\s+year|year)[\s\-_/.']*\d{2,4}(?:[/\-]\d{2,4})?|\d{4}(?:\s*fy)?)$", re.I
)


def coarse_period_warning(raw) -> str | None:
    """Explain why a quarter/year label cannot be imported as a month."""
    label = str(raw).strip()
    if _QUARTER_LABEL.fullmatch(label):
        kind, example = "quarterly", "Q1 2025"
    elif _YEAR_LABEL.fullmatch(label):
        kind, example = "yearly", "FY24"
    else:
        return None
    return (f"This file has {kind} figures (e.g. {example}). CloseBrief works month by month: "
            "export monthly figures instead. Quarters and years are added up for you in the Q and Y views.")


def import_period_warnings(df: pd.DataFrame, mapping: MappingSpec) -> list[str]:
    """Inspect every mapped source period, including wide headers and literals."""
    periods = [mapping.period_literal] if mapping.period_literal else []
    if mapping.layout == "wide":
        periods += mapping.wide_period_cols
    elif not mapping.period_literal and mapping.period_col in df.columns:
        periods += df[mapping.period_col].dropna().unique().tolist()
    return list(dict.fromkeys(w for p in periods if (w := coarse_period_warning(p))))


_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1
)}


def parse_period(raw: str) -> str:
    """Parse 'Jan-25', '2025-03', 'Q1 2025', 'FY24', 'Mar 2025', '03/2025',
    or a full date to canonical 'YYYY-MM' (quarters -> first month)."""
    s = str(raw).strip()

    m = re.match(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-_/.']*(\d{2,4})$", s, re.I)
    if m:
        month = _MONTHS[m.group(1).lower()[:3]]
        year = int(m.group(2))
        year += 2000 if year < 100 else 0
        return f"{year:04d}-{month:02d}"

    m = re.match(r"^(\d{4})[\s\-_/.]?(0?[1-9]|1[0-2])$", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"

    m = re.match(r"^(0?[1-9]|1[0-2])[\s\-_/.](\d{4})$", s)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"

    m = re.match(r"^q([1-4])[\s\-_/.']*(\d{2,4})$", s, re.I)
    if m:
        year = int(m.group(2))
        year += 2000 if year < 100 else 0
        return f"{year:04d}-{(int(m.group(1)) - 1) * 3 + 1:02d}"

    m = re.match(r"^fy[\s\-_/.']*(\d{2,4})$", s, re.I)
    if m:
        year = int(m.group(1))
        year += 2000 if year < 100 else 0
        return f"{year:04d}-01"

    parsed = pd.to_datetime(s, errors="coerce")
    if pd.notna(parsed):
        return f"{parsed.year:04d}-{parsed.month:02d}"

    raise MappingError(f"Could not parse '{raw}' as a period")


def coerce_value(raw) -> float | None:
    """'$1,234.50' -> 1234.5; '(500)' -> -500.0; '' / NaN -> None."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "-", "–"):
        return None
    negative = bool(re.match(r"^\(.*\)$", s))
    s = re.sub(r"^\((.*)\)$", r"\1", s)
    s = re.sub(r"[\$€£,\s%]", "", s)
    try:
        v = float(s)
    except ValueError:
        return None
    if not math.isfinite(v):
        raise MappingError("Numeric values must be finite")
    return -v if negative else v


_MISSING = {
    "period_col": "No column is set as the month or date. Pick the column that says which month each row "
                  "belongs to, or choose the month the whole file covers.",
    "value_col": "No column is set as the amount to track. Pick the column with the actual values.",
    "metric_col": "No column is set as the KPI name.",
}


def _require(mapping: MappingSpec, field: str) -> str:
    v = getattr(mapping, field)
    if not v:
        raise MappingError(_MISSING.get(field, f"Mapping is missing required field '{field}' for layout={mapping.layout}"))
    return v


def _check_col(df: pd.DataFrame, col: str, what: str) -> None:
    if col not in df.columns:
        raise MappingError(f"{what} column '{col}' not found in file")


def _check_numeric(df: pd.DataFrame, col: str, what: str) -> None:
    coerced = df[col].map(coerce_value)
    non_null_src = df[col].notna() & (df[col].astype(str).str.strip() != "")
    if non_null_src.any() and coerced[non_null_src].isna().mean() > 0.2:
        raise MappingError(f"{what} column '{col}' is not numeric (values like '{df[col][non_null_src].iloc[0]}')")


_TOTAL_LABEL = re.compile(r"^\s*(sub)?total\b|^\s*grand total\b", re.I)


_FORMULA_LEAD = ("=", "+", "@")  # leading '-' is left alone: it means negative


def sanitize_label(value) -> str:
    """Neutralize CSV/Excel formula injection in a text label that will be
    stored and may later be re-exported: strip a leading =/+/@ so the cell
    can't execute in a downstream spreadsheet (hardening, PRD US-J2)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    if s.lower() in ("nan", "none", "nat"):
        return ""
    while s and s[0] in _FORMULA_LEAD:
        s = s[1:].lstrip()
    return s


def likely_total_rows(df: pd.DataFrame, mapping: MappingSpec) -> list[dict]:
    """The shared summary-row rule for schema preview and confirmed imports.

    A marker in any text column counts, even when that column isn't mapped.
    Explicitly kept metric labels override both markers and blank IDs.
    """
    if df.empty:
        return []
    metric_col = mapping.metric_col if mapping.layout == "long" else mapping.wide_metric_col
    fallback = sanitize_label(mapping.wide_value_label or mapping.value_col or "")
    labels = (df[metric_col].map(sanitize_label) if metric_col in df.columns
              else pd.Series(fallback, index=df.index))
    is_total = pd.Series(False, index=df.index)
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        col_str = df[col].map(lambda v: "" if pd.isna(v) else str(v))
        is_total = is_total | col_str.map(lambda s: bool(_TOTAL_LABEL.match(s)))
    blank_summary_id = pd.Series(False, index=df.index)
    if metric_col in df.columns and mapping.id_col in df.columns:
        ids = df[mapping.id_col]
        blank_summary_id = (ids.isna() | (ids.astype(str).str.strip() == "")) & (labels != "")
    kept = labels.isin({sanitize_label(label) for label in mapping.keep_labels})
    return [
        {"row": int(index) + 1, "label": labels.loc[index],
         "reason": "starts with Total" if is_total.loc[index] else "blank ID on a summary row"}
        for index in df.index[(is_total | blank_summary_id) & ~kept]
    ]


def _drop_total_rows(df: pd.DataFrame, mapping: MappingSpec) -> pd.DataFrame:
    totals = likely_total_rows(df, mapping)
    drop_indices = {row["row"] - 1 for row in totals}
    drop = pd.Series(df.index.isin(drop_indices), index=df.index)
    metric_col = mapping.metric_col if mapping.layout == "long" else mapping.wide_metric_col
    if metric_col in df.columns:
        # Empty labels aren't metrics; retain the existing section/blank cleanup.
        drop |= df[metric_col].map(sanitize_label) == ""
    out = df[~drop].copy()
    out.attrs["totals_dropped"] = totals
    return out


def _duplicate_source_rows(df: pd.DataFrame, mapping: MappingSpec) -> set[int]:
    """Compare mapped source cells before coercion or melting loses identity."""
    columns = [mapping.id_col, *mapping.dimension_cols]
    if mapping.layout == "long":
        columns += [mapping.metric_col, mapping.value_col, *mapping.value_cols,
                    mapping.budget_col, mapping.quantity_col, mapping.price_col,
                    mapping.budget_quantity_col, mapping.budget_price_col]
        if not mapping.period_literal:
            columns.append(mapping.period_col)
    else:
        columns += [mapping.wide_metric_col, *mapping.wide_period_cols, *mapping.wide_budget_cols]
    columns = list(dict.fromkeys(c for c in columns if c and c in df.columns))
    return set(df.index[df.duplicated(subset=columns)]) if columns else set()


def normalize(df: pd.DataFrame, mapping: MappingSpec) -> pd.DataFrame:
    """Return the canonical long-format DataFrame."""
    df = df.copy()  # Upload cleanup may have removed blank rows; retain source positions.
    rows_read = len(df)
    period_warnings = import_period_warnings(df, mapping)
    if period_warnings:
        raise MappingError(period_warnings[0])
    if mapping.layout == "long":
        for field in ("metric_col", "quantity_col", "price_col", "budget_quantity_col", "budget_price_col"):
            column = getattr(mapping, field)
            if column:
                _check_col(df, column, field)
        literal_period = parse_period(mapping.period_literal) if mapping.period_literal else None
        period_col = None if literal_period else _require(mapping, "period_col")
        if not mapping.value_col and not mapping.value_cols:
            raise MappingError(_MISSING["value_col"])
        if period_col:
            _check_col(df, period_col, "Period")
        if mapping.budget_col:
            _check_col(df, mapping.budget_col, "Budget")
            _check_numeric(df, mapping.budget_col, "Budget")
        if mapping.id_col:
            _check_col(df, mapping.id_col, "Id")
        df = _drop_total_rows(df, mapping)
        duplicate_rows = _duplicate_source_rows(df, mapping)
        if period_col:
            df = df[df[period_col].notna()]

        def _periods_for(frame: pd.DataFrame) -> pd.Series:
            if literal_period:
                return pd.Series(literal_period, index=frame.index)
            return frame[period_col].map(parse_period)

        if mapping.value_cols:
            # Metric x value cross-product: one numeric column per measure, all
            # crossed with the metric-name column (e.g. 10 channels x [spend,
            # clicks, ...] -> "<channel> Spend", "<channel> Clicks", ...). Each
            # value column becomes its own set of rows, sharing period/budget/
            # dimensions from the same source row.
            for c in mapping.value_cols:
                _check_col(df, c, "Value")
                _check_numeric(df, c, "Value")
            parts = []
            for vcol in mapping.value_cols:
                part = pd.DataFrame()
                part["period"] = _periods_for(df)
                part["_source_row"] = df.index + 1
                base_label = df[mapping.metric_col].map(sanitize_label) if mapping.metric_col else ""
                suffix = sanitize_label(vcol)
                part["metric"] = (base_label + " " + suffix).str.strip() if mapping.metric_col else suffix
                part["value"] = df[vcol].map(coerce_value)
                part["budget"] = df[mapping.budget_col].map(coerce_value) if mapping.budget_col else None
                part["quantity"] = None
                part["price"] = None
                part["budget_quantity"] = None
                part["budget_price"] = None
                part["id"] = None
                if mapping.dimension_cols:
                    dims = [c for c in mapping.dimension_cols if c in df.columns]
                    part["dimensions"] = df[dims].astype(str).apply(
                        lambda r: json.dumps(dict(zip(dims, r))), axis=1
                    ) if dims else None
                else:
                    part["dimensions"] = None
                parts.append(part)
            out = pd.concat(parts, ignore_index=True)
        else:
            value_col = mapping.value_col
            _check_col(df, value_col, "Value")
            _check_numeric(df, value_col, "Value")
            out = pd.DataFrame()
            out["period"] = _periods_for(df)
            out["_source_row"] = df.index + 1
            if mapping.metric_col:
                _check_col(df, mapping.metric_col, "Metric")
                out["metric"] = df[mapping.metric_col].map(sanitize_label)
            else:
                out["metric"] = sanitize_label(mapping.wide_value_label or value_col)
            out["value"] = df[value_col].map(coerce_value)
            out["budget"] = df[mapping.budget_col].map(coerce_value) if mapping.budget_col else None
            out["quantity"] = df[mapping.quantity_col].map(coerce_value) if mapping.quantity_col else None
            out["price"] = df[mapping.price_col].map(coerce_value) if mapping.price_col else None
            out["budget_quantity"] = (
                df[mapping.budget_quantity_col].map(coerce_value) if mapping.budget_quantity_col else None
            )
            out["budget_price"] = (
                df[mapping.budget_price_col].map(coerce_value) if mapping.budget_price_col else None
            )
            out["id"] = df[mapping.id_col].map(sanitize_label) if mapping.id_col else None
            if mapping.dimension_cols:
                for c in mapping.dimension_cols:
                    _check_col(df, c, "Dimension")
                out["dimensions"] = df[mapping.dimension_cols].astype(str).apply(
                    lambda r: json.dumps(dict(zip(mapping.dimension_cols, r))), axis=1
                )
            else:
                out["dimensions"] = None

    else:  # wide
        if not mapping.wide_period_cols:
            raise MappingError("Mapping is missing 'wide_period_cols' for layout=wide")
        for c in mapping.wide_period_cols:
            _check_col(df, c, "Period")
        if mapping.wide_budget_cols and len(mapping.wide_budget_cols) != len(mapping.wide_period_cols):
            raise MappingError(
                "wide_budget_cols must be the same length/order as wide_period_cols"
            )
        if mapping.id_col:
            _check_col(df, mapping.id_col, "Id")
        df = _drop_total_rows(df, mapping)
        duplicate_rows = _duplicate_source_rows(df, mapping)

        id_vars = []
        if mapping.wide_metric_col:
            _check_col(df, mapping.wide_metric_col, "Metric")
            id_vars.append(mapping.wide_metric_col)
        if mapping.id_col and mapping.id_col not in id_vars:
            id_vars.append(mapping.id_col)
        id_vars += [c for c in mapping.dimension_cols if c in df.columns]

        melted = df.melt(
            id_vars=id_vars, value_vars=mapping.wide_period_cols,
            var_name="_period_raw", value_name="_value_raw",
        )
        out = pd.DataFrame()
        out["period"] = melted["_period_raw"].map(parse_period)
        out["_source_row"] = list(df.index + 1) * len(mapping.wide_period_cols)
        if mapping.wide_metric_col:
            out["metric"] = melted[mapping.wide_metric_col].map(sanitize_label)
        else:
            if not mapping.wide_value_label:
                raise MappingError("Wide layout needs 'wide_metric_col' or 'wide_value_label'")
            out["metric"] = sanitize_label(mapping.wide_value_label)
        out["value"] = melted["_value_raw"].map(coerce_value)

        if mapping.wide_budget_cols:
            for c in mapping.wide_budget_cols:
                _check_col(df, c, "Budget")
            # melt stacks value_vars column-by-column, so the budget melt lines
            # up row-for-row with the value melt (same id_vars, parallel order).
            budget_melt = df.melt(
                id_vars=id_vars, value_vars=mapping.wide_budget_cols,
                var_name="_bcol", value_name="_budget_raw",
            )
            out["budget"] = budget_melt["_budget_raw"].map(coerce_value).values
        else:
            out["budget"] = None
        out["quantity"] = None
        out["price"] = None
        out["budget_quantity"] = None
        out["budget_price"] = None
        out["id"] = melted[mapping.id_col].map(sanitize_label) if mapping.id_col else None
        if mapping.dimension_cols:
            dims = [c for c in mapping.dimension_cols if c in melted.columns]
            out["dimensions"] = melted[dims].map(sanitize_label).apply(
                lambda r: json.dumps(dict(zip(dims, r))), axis=1
            ) if dims else None
        else:
            out["dimensions"] = None

    # Capture the full metric set BEFORE dropping null-value rows: a metric
    # that legitimately has data in zero periods (e.g. "<channel> Clicks" for
    # every channel except the one actually tracked) must still exist as a
    # metric with null facts — not silently vanish because every one of its
    # rows happened to be empty. store_normalized reads this via out.attrs.
    all_metrics = sorted(str(m) for m in out["metric"].dropna().unique())

    # Identical lines are certainly one line exported twice only when they also
    # share a row ID. Without an ID column, two identical rows can be two real
    # sales of the same amount: they're kept (and added), and reported so the
    # user can check for a double export, rather than silently understating.
    identical_kept = set() if mapping.id_col else set(duplicate_rows)
    if not mapping.id_col:
        duplicate_rows = set()

    def _examples(rows: set[int]) -> list[dict]:
        hits = out[out["_source_row"].isin({i + 1 for i in rows})]
        return [{"row": int(r["_source_row"]), "metric": r["metric"], "period": r["period"]}
                for _, r in hits.drop_duplicates("_source_row").head(20).iterrows()]

    examples, kept_examples = _examples(duplicate_rows), _examples(identical_kept)
    out = out[~out["_source_row"].isin({i + 1 for i in duplicate_rows})]
    out = out[out["value"].notna()].reset_index(drop=True)
    if out.empty:
        raise MappingError("Mapping produced no valid rows — check the value column and layout")
    out.attrs["all_metrics"] = all_metrics
    out.attrs["import_report"] = {
        "rows_read": rows_read, "rows_kept": int(out["_source_row"].nunique()),
        "duplicates_dropped": {"count": len(duplicate_rows), "examples": examples},
        "identical_kept": {"count": len(identical_kept), "examples": kept_examples},
        "totals_dropped": df.attrs["totals_dropped"],
    }
    out = out.drop(columns="_source_row")
    return out


def _normalize_label(s: str) -> str:
    """Fold a label to a comparison key: case, surrounding whitespace, and the
    "/" vs " " / "-" vs " " punctuation that spreadsheet authors drift on
    (Content/Seo vs Content/SEO, Paid Social - Linkedin vs ...LinkedIn) don't
    change what the label means."""
    s = str(s).strip().lower()
    s = re.sub(r"[\s/_-]+", " ", s)
    return s.strip()


def match_budget_to_dataset(
    conn: sqlite3.Connection, canonical: pd.DataFrame, dataset_id: int,
) -> dict:
    """Join a budget-only canonical frame onto an EXISTING dataset's metrics,
    by (metric identity, period) — never creates a new metric or a new
    metric_values row. A budget file is a correction/addition to data that
    already exists, not a new import.

    Matching tries, in order: (1) external_id — a GL code or SKU carried in
    the upload's `id` column against the metric's stored external_id, the
    most reliable key because a code doesn't drift the way a text label can;
    (2) exact metric name; (3) a normalized (case/punctuation-insensitive)
    fallback so "Content/Seo" still finds "Content/SEO" without either file
    having been fixed by hand. Returns a report — matched, near-miss (matched
    only via id or normalization, so the caller can show what would be
    linked and let the user confirm), and unmatched (no existing metric at
    all, or a period this dataset has no actuals row for) — rather than
    silently dropping any of the three cases.
    """
    from app.datasets import mark_data_changed

    rows = conn.execute(
        "SELECT id, name, external_id FROM metrics WHERE dataset_id = ?", (dataset_id,)
    ).fetchall()
    by_exact = {r["name"]: r["id"] for r in rows}
    by_external_id = {r["external_id"]: (r["name"], r["id"]) for r in rows if r["external_id"]}
    by_normalized: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        by_normalized.setdefault(_normalize_label(r["name"]), []).append((r["name"], r["id"]))

    has_id_col = "id" in canonical.columns

    matched = 0
    near_misses: list[dict] = []
    unmatched_metric: set[str] = set()
    unmatched_period: list[dict] = []
    seen_near_miss: set[str] = set()
    written_pairs = set()

    group_cols = ["metric", "period", "id"] if has_id_col else ["metric", "period"]
    for key, group in canonical.groupby(group_cols, dropna=False):
        metric, period = key[0], key[1]
        upload_id = key[2] if has_id_col else None
        budget = None
        for v in group["value"]:
            if v is not None and pd.notna(v):
                budget = float(v) if budget is None else budget + float(v)
        if budget is None:
            continue

        metric_id = None
        matched_name = metric
        via_id = False
        if upload_id and pd.notna(upload_id) and str(upload_id).strip():
            hit = by_external_id.get(str(upload_id).strip())
            if hit:
                matched_name, metric_id = hit
                via_id = True
        if metric_id is None:
            metric_id = by_exact.get(metric)
        if metric_id is None:
            candidates = by_normalized.get(_normalize_label(metric), [])
            if len(candidates) == 1:
                matched_name, metric_id = candidates[0]
        if metric_id is None:
            unmatched_metric.add(metric)
            continue
        if not via_id and matched_name != metric and metric not in seen_near_miss:
            near_misses.append({"upload_label": metric, "matched_metric": matched_name})
            seen_near_miss.add(metric)

        existing = conn.execute(
            "SELECT id FROM metric_values WHERE metric_id = ? AND period = ?",
            (metric_id, period),
        ).fetchone()
        if existing is None:
            # No actuals row for this metric+period -> nowhere to attach a
            # budget value. Reported, never inserted as a value-less row
            # (metric_values.value is NOT NULL by design: every row is a real
            # observation, and a budget-only fact has no observation yet).
            unmatched_period.append({"metric": matched_name, "period": period})
            continue

        conn.execute(
            "UPDATE metric_values SET budget = ? WHERE id = ?", (budget, existing["id"])
        )
        matched += 1
        written_pairs.add((matched_name, period))

    if matched:
        mark_data_changed(conn, dataset_id)
    conn.commit()
    canonical.attrs["written_pairs"] = written_pairs
    return {
        "matched": matched,
        "near_misses": near_misses,
        "unmatched_metrics": sorted(unmatched_metric),
        "unmatched_periods": unmatched_period,
    }


def _num(v):
    return float(v) if v is not None and pd.notna(v) else None


def store_normalized(conn: sqlite3.Connection, canonical: pd.DataFrame, dataset_id: int) -> dict:
    """Aggregate canonical rows to one row per metric+period, then upsert into
    metric_values. Dimension-level rows (e.g. revenue by product) are SUMMED to
    the metric total rather than overwriting each other; when those rows carry
    quantity/price detail, a true multi-item Price/Volume/Mix bridge is computed
    from them and stored in pvm_bridges before the collapse."""
    from app.compute.pvm import PVMItem, decompose
    from app.datasets import get_or_create_metric, mark_data_changed

    cur = conn.cursor()
    # canonical["metric"].unique() only has metrics that survived the null-value
    # drop in normalize(); attrs["all_metrics"] (when present) is the full set
    # BEFORE that drop, so a metric with zero populated periods (e.g. one value
    # column tracked for only one dimension value) still gets created rather
    # than silently not existing.
    metric_names = {
        n for n in set(canonical.attrs.get("all_metrics", [])) | set(canonical["metric"].unique())
        if n is not None and pd.notna(n) and str(n).strip()
    }
    external_ids: dict = {}
    if "id" in canonical.columns:
        for name, group in canonical.groupby("metric")["id"]:
            first = next((v for v in group if v is not None and pd.notna(v) and str(v).strip()), None)
            if first is not None:
                external_ids[name] = str(first)
    metric_ids = {
        name: get_or_create_metric(conn, dataset_id, name, external_id=external_ids.get(name))
        for name in sorted(metric_names)
    }
    conn.commit()

    def _sum(series):
        vals = [v for v in series if v is not None and pd.notna(v)]
        return float(sum(vals)) if vals else None

    n_written = 0
    for (metric, period), group in canonical.groupby(["metric", "period"]):
        metric_id = metric_ids[metric]
        value = _sum(group["value"])
        budget = _sum(group["budget"])
        quantity = _sum(group["quantity"])
        budget_quantity = _sum(group["budget_quantity"])

        # Multi-item PVM: one item per dimension row that has full qty+price.
        items = []
        for _, r in group.iterrows():
            aq, bq = _num(r["quantity"]), _num(r["budget_quantity"])
            ap, bp = _num(r["price"]), _num(r["budget_price"])
            if None not in (aq, bq, ap, bp):
                items.append(PVMItem(actual_qty=aq, budget_qty=bq, actual_price=ap, budget_price=bp))
        bridge = decompose(items) if items else None

        # Aggregate-level price/budget_price only meaningful for a single item;
        # for multiple items leave null (the bridge carries the decomposition).
        price = _num(group["price"].iloc[0]) if len(group) == 1 else None
        budget_price = _num(group["budget_price"].iloc[0]) if len(group) == 1 else None
        dims = group["dimensions"].iloc[0] if len(group) == 1 else None

        cur.execute(
            """
            INSERT INTO metric_values (metric_id, period, value, budget, quantity, price,
                                       budget_quantity, budget_price, dimensions, source_rows)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metric_id, period) DO UPDATE SET
                value=excluded.value, budget=excluded.budget,
                quantity=excluded.quantity, price=excluded.price,
                budget_quantity=excluded.budget_quantity, budget_price=excluded.budget_price,
                dimensions=excluded.dimensions, source_rows=excluded.source_rows
            """,
            (metric_id, period, float(value), budget, quantity, price,
             budget_quantity, budget_price, dims if isinstance(dims, str) else None, len(group)),
        )

        if bridge is not None:
            cur.execute(
                """
                INSERT INTO pvm_bridges (metric_id, period, volume, price, mix, total, n_items)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(metric_id, period) DO UPDATE SET
                    volume=excluded.volume, price=excluded.price, mix=excluded.mix,
                    total=excluded.total, n_items=excluded.n_items
                """,
                (metric_id, period, bridge.volume, bridge.price, bridge.mix,
                 bridge.total, len(items)),
            )
        n_written += 1
    if n_written:
        mark_data_changed(conn, dataset_id)
    conn.commit()
    return {
        "rows_normalized": n_written,
        "import_report": {
            **canonical.attrs.get("import_report", {
                "rows_read": len(canonical), "rows_kept": len(canonical),
                "duplicates_dropped": {"count": 0, "examples": []},
                "identical_kept": {"count": 0, "examples": []}, "totals_dropped": [],
            }),
            "combined": [
                {"metric": metric, "period": period, "rows": int(count)}
                for (metric, period), count in canonical.groupby(["metric", "period"]).size().items() if count > 1
            ][:50],
        },
        "metrics": sorted(metric_names),
        "periods": sorted(canonical["period"].unique().tolist()),
    }
