"""Stages 3-4 — Apply a confirmed mapping and normalize to the canonical
long format (Addendum v1.1 Sections 1.5-1.7):

    period | metric | value | budget | quantity | price | dimensions

Handles both layouts. Wide (months as columns — the common real FP&A export)
is melted; long is renamed. Values are coerced with currency symbols, commas,
and accounting negatives ("(1,200)" -> -1200) stripped. Periods are parsed to
"YYYY-MM". No column name is hardcoded — everything comes from the mapping.
"""

import json
import re
import sqlite3
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field


class MappingSpec(BaseModel):
    layout: str = Field(pattern="^(long|wide)$")
    # long layout
    period_col: Optional[str] = None
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
    # wide layout
    wide_period_cols: list[str] = Field(default_factory=list)
    wide_value_label: Optional[str] = None
    wide_metric_col: Optional[str] = None  # optional: metric names in a column
    # Optional budget columns parallel to wide_period_cols, same order, so a
    # wide layout can carry budget instead of nulling the variance story.
    wide_budget_cols: list[str] = Field(default_factory=list)


class MappingError(Exception):
    """Validation failure. Message names the specific problem column."""


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
    return -v if negative else v


def _require(mapping: MappingSpec, field: str) -> str:
    v = getattr(mapping, field)
    if not v:
        raise MappingError(f"Mapping is missing required field '{field}' for layout={mapping.layout}")
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


def _is_total_row(label: str, id_value=None, id_col_present: bool = False) -> bool:
    """A summary/total row masquerading as a data row: its label starts with
    'Total' (accounts for 'Total Operating Expenses', 'Subtotal', 'Grand
    Total'), or — when the mapping has a secondary identifier column (e.g. GL
    Code) — the identifier is blank while the metric label isn't (real line
    items always carry an id; roll-up rows don't)."""
    if _TOTAL_LABEL.match(str(label)):
        return True
    if id_col_present and (id_value is None or (isinstance(id_value, float) and pd.isna(id_value))
                           or str(id_value).strip() == ""):
        return True
    return False


_FORMULA_LEAD = ("=", "+", "@")  # leading '-' is left alone: it means negative


def sanitize_label(value) -> str:
    """Neutralize CSV/Excel formula injection in a text label that will be
    stored and may later be re-exported: strip a leading =/+/@ so the cell
    can't execute in a downstream spreadsheet (hardening, PRD US-J2)."""
    s = str(value).strip()
    while s and s[0] in _FORMULA_LEAD:
        s = s[1:].lstrip()
    return s


def _drop_total_rows(df: pd.DataFrame, metric_col: str | None, id_col: str | None) -> pd.DataFrame:
    """Remove roll-up/section rows (a 'Total' line, a 'REVENUE' section label
    with no data, an account whose id is blank because it's a subtotal) before
    they're normalized into a fake metric. Source-format cleanup, not domain
    logic — belongs before period/value coercion, not after."""
    if metric_col is None or metric_col not in df.columns:
        return df
    label = df[metric_col]
    is_total = label.astype(str).map(lambda s: bool(_TOTAL_LABEL.match(s)))
    if id_col and id_col in df.columns:
        id_blank = df[id_col].isna() | (df[id_col].astype(str).str.strip() == "")
        is_total = is_total | (id_blank & label.notna() & (label.astype(str).str.strip() != ""))
    return df[~is_total]


def normalize(df: pd.DataFrame, mapping: MappingSpec) -> pd.DataFrame:
    """Return the canonical long-format DataFrame."""
    if mapping.layout == "long":
        period_col = _require(mapping, "period_col")
        if not mapping.value_col and not mapping.value_cols:
            raise MappingError("Mapping is missing required field 'value_col' for layout=long")
        _check_col(df, period_col, "Period")
        if mapping.budget_col:
            _check_col(df, mapping.budget_col, "Budget")
            _check_numeric(df, mapping.budget_col, "Budget")
        if mapping.id_col:
            _check_col(df, mapping.id_col, "Id")
        df = _drop_total_rows(df, mapping.metric_col, mapping.id_col)

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
                part["period"] = df[period_col].map(parse_period)
                base_label = df[mapping.metric_col].map(sanitize_label) if mapping.metric_col else ""
                suffix = sanitize_label(vcol)
                part["metric"] = (base_label + " " + suffix).str.strip() if mapping.metric_col else suffix
                part["value"] = df[vcol].map(coerce_value)
                part["budget"] = df[mapping.budget_col].map(coerce_value) if mapping.budget_col else None
                part["quantity"] = None
                part["price"] = None
                part["budget_quantity"] = None
                part["budget_price"] = None
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
            out["period"] = df[period_col].map(parse_period)
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
        df = _drop_total_rows(df, mapping.wide_metric_col, mapping.id_col)

        id_vars = []
        if mapping.wide_metric_col:
            _check_col(df, mapping.wide_metric_col, "Metric")
            id_vars.append(mapping.wide_metric_col)
        id_vars += [c for c in mapping.dimension_cols if c in df.columns]

        melted = df.melt(
            id_vars=id_vars, value_vars=mapping.wide_period_cols,
            var_name="_period_raw", value_name="_value_raw",
        )
        out = pd.DataFrame()
        out["period"] = melted["_period_raw"].map(parse_period)
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
    out = out[out["value"].notna()].reset_index(drop=True)
    if out.empty:
        raise MappingError("Mapping produced no valid rows — check the value column and layout")
    out.attrs["all_metrics"] = all_metrics
    return out


def _num(v):
    return float(v) if v is not None and pd.notna(v) else None


def store_normalized(conn: sqlite3.Connection, canonical: pd.DataFrame, dataset_id: int) -> dict:
    """Aggregate canonical rows to one row per metric+period, then upsert into
    metric_values. Dimension-level rows (e.g. revenue by product) are SUMMED to
    the metric total rather than overwriting each other; when those rows carry
    quantity/price detail, a true multi-item Price/Volume/Mix bridge is computed
    from them and stored in pvm_bridges before the collapse."""
    from app.compute.pvm import PVMItem, decompose
    from app.datasets import get_or_create_metric

    cur = conn.cursor()
    # canonical["metric"].unique() only has metrics that survived the null-value
    # drop in normalize(); attrs["all_metrics"] (when present) is the full set
    # BEFORE that drop, so a metric with zero populated periods (e.g. one value
    # column tracked for only one dimension value) still gets created rather
    # than silently not existing.
    metric_names = set(canonical.attrs.get("all_metrics", [])) | set(canonical["metric"].unique())
    metric_ids = {
        name: get_or_create_metric(conn, dataset_id, name)
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
                                       budget_quantity, budget_price, dimensions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metric_id, period) DO UPDATE SET
                value=excluded.value, budget=excluded.budget,
                quantity=excluded.quantity, price=excluded.price,
                budget_quantity=excluded.budget_quantity, budget_price=excluded.budget_price,
                dimensions=excluded.dimensions
            """,
            (metric_id, period, float(value), budget, quantity, price,
             budget_quantity, budget_price, dims if isinstance(dims, str) else None),
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
    conn.commit()
    return {
        "rows_normalized": n_written,
        "metrics": sorted(metric_names),
        "periods": sorted(canonical["period"].unique().tolist()),
    }
