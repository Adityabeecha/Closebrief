"""Tests for v1.2 ingestion fixes: wide-layout budget columns, dimension
aggregation with multi-item P/V/M, formula-injection sanitizing, .xls guard."""

import sqlite3

import pandas as pd
import pytest

from app.ingestion.mapping import MappingError, MappingSpec, normalize, sanitize_label, store_normalized
from app.ingestion.upload import UploadError, _read_frame

SCHEMA = """
CREATE TABLE datasets (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, source_upload_id TEXT,
    is_active INTEGER DEFAULT 0, uploaded_by TEXT, uploaded_by_email TEXT,
    is_demo INTEGER DEFAULT 0, workspace_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id INTEGER, name TEXT,
    category TEXT DEFAULT 'Uncategorized', unit TEXT DEFAULT 'USD', direction_good TEXT DEFAULT 'up',
    external_id TEXT, UNIQUE(dataset_id, name));
CREATE TABLE metric_values (id INTEGER PRIMARY KEY AUTOINCREMENT, metric_id INTEGER, period TEXT,
    value REAL, budget REAL, quantity REAL, price REAL, budget_quantity REAL, budget_price REAL,
    dimensions TEXT, UNIQUE(metric_id, period));
CREATE TABLE pvm_bridges (id INTEGER PRIMARY KEY AUTOINCREMENT, metric_id INTEGER, period TEXT,
    volume REAL, price REAL, mix REAL, total REAL, n_items INTEGER, UNIQUE(metric_id, period));
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    from app.datasets import create_dataset
    dataset_id = create_dataset(c, "test")
    yield c, dataset_id
    c.close()


# ---------- formula injection ----------

def test_sanitize_label_strips_formula_leads():
    assert sanitize_label("=SUM(A1:A9)") == "SUM(A1:A9)"
    assert sanitize_label("@cmd") == "cmd"
    assert sanitize_label("+val") == "val"
    assert sanitize_label("Net Revenue") == "Net Revenue"
    assert sanitize_label("-5") == "-5"  # negative numbers unharmed


def test_metric_name_sanitized_on_normalize():
    df = pd.DataFrame({"Month": ["Jan-25"], "Item": ["=cmd|Rev"], "Actual": ["100"]})
    out = normalize(df, MappingSpec(layout="long", period_col="Month",
                                    metric_col="Item", value_col="Actual"))
    assert out.loc[0, "metric"] == "cmd|Rev"


# ---------- wide-layout budget columns ----------

def test_wide_budget_columns_map():
    df = pd.DataFrame({
        "Line": ["Revenue"],
        "Jan-25": ["100"], "Feb-25": ["110"],
        "Jan-25 Bud": ["90"], "Feb-25 Bud": ["105"],
    })
    out = normalize(df, MappingSpec(
        layout="wide", wide_metric_col="Line",
        wide_period_cols=["Jan-25", "Feb-25"],
        wide_budget_cols=["Jan-25 Bud", "Feb-25 Bud"],
    ))
    jan = out[out["period"] == "2025-01"].iloc[0]
    feb = out[out["period"] == "2025-02"].iloc[0]
    assert jan["value"] == 100 and jan["budget"] == 90
    assert feb["value"] == 110 and feb["budget"] == 105


def test_wide_budget_length_mismatch_errors():
    df = pd.DataFrame({"Line": ["Rev"], "Jan-25": ["1"], "Feb-25": ["2"], "B": ["1"]})
    with pytest.raises(MappingError, match="same length"):
        normalize(df, MappingSpec(layout="wide", wide_metric_col="Line",
                                  wide_period_cols=["Jan-25", "Feb-25"], wide_budget_cols=["B"]))


# ---------- dimension aggregation + multi-item PVM ----------

def test_dimension_rows_aggregate_not_overwrite(conn):
    # Two products, same metric+period: value must SUM to 300, not keep last.
    canonical = pd.DataFrame([
        {"metric": "Revenue", "period": "2025-01", "value": 100, "budget": 90,
         "quantity": None, "price": None, "budget_quantity": None, "budget_price": None,
         "dimensions": '{"product": "A"}'},
        {"metric": "Revenue", "period": "2025-01", "value": 200, "budget": 180,
         "quantity": None, "price": None, "budget_quantity": None, "budget_price": None,
         "dimensions": '{"product": "B"}'},
    ])
    c, ds = conn
    summary = store_normalized(c, canonical, dataset_id=ds)
    assert summary["rows_normalized"] == 1
    row = c.execute("SELECT value, budget FROM metric_values").fetchone()
    assert row["value"] == 300 and row["budget"] == 270


def test_multi_item_pvm_bridge_stored(conn):
    canonical = pd.DataFrame([
        {"metric": "Revenue", "period": "2025-01", "value": 250, "budget": 500,
         "quantity": 50, "price": 5, "budget_quantity": 100, "budget_price": 5,
         "dimensions": '{"product": "cheap"}'},
        {"metric": "Revenue", "period": "2025-01", "value": 3000, "budget": 2000,
         "quantity": 150, "price": 20, "budget_quantity": 100, "budget_price": 20,
         "dimensions": '{"product": "premium"}'},
    ])
    c, ds = conn
    store_normalized(c, canonical, dataset_id=ds)
    b = c.execute("SELECT volume, price, mix, total, n_items FROM pvm_bridges").fetchone()
    assert b["n_items"] == 2
    # volume+price+mix reconciles to total
    assert abs((b["volume"] + b["price"] + b["mix"]) - b["total"]) < 0.01


# ---------- .xls guard ----------

def test_xls_gives_clear_error():
    with pytest.raises(UploadError, match="Legacy .xls"):
        _read_frame(b"\xd0\xcf\x11\xe0", "old.xls")
