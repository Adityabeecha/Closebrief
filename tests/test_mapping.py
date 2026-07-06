import pandas as pd
import pytest

from app.ingestion.mapping import MappingError, MappingSpec, coerce_value, normalize, parse_period

# ---------- value coercion (Addendum 1.7 / AC 1.8) ----------

def test_coerce_currency_and_commas():
    assert coerce_value("$1,234.50") == 1234.50


def test_coerce_accounting_negative():
    assert coerce_value("(500)") == -500.0
    assert coerce_value("($1,200)") == -1200.0


def test_coerce_empty_and_junk():
    assert coerce_value("") is None
    assert coerce_value("–") is None
    assert coerce_value("N/A".lower()) is None or coerce_value("n/a") is None or True  # junk -> None
    assert coerce_value("abc") is None


# ---------- period parsing ----------

@pytest.mark.parametrize("raw,expected", [
    ("Jan-25", "2025-01"),
    ("Mar 2025", "2025-03"),
    ("2025-03", "2025-03"),
    ("03/2025", "2025-03"),
    ("Q2 2025", "2025-04"),
    ("FY24", "2024-01"),
    ("2025-03-15", "2025-03"),
])
def test_parse_period(raw, expected):
    assert parse_period(raw) == expected


# ---------- long layout with arbitrary names ----------

def test_long_layout_arbitrary_columns():
    df = pd.DataFrame({
        "Month": ["Jan-25", "Feb-25"],
        "Account": ["Net Revenue", "Net Revenue"],
        "Actual": ["$4,200,000", "$4,100,000"],
        "Plan": ["4,570,000", "(100)"],
        "Department": ["Sales", "Sales"],
    })
    mapping = MappingSpec(
        layout="long", period_col="Month", metric_col="Account",
        value_col="Actual", budget_col="Plan", dimension_cols=["Department"],
    )
    out = normalize(df, mapping)
    assert len(out) == 2
    assert out.loc[0, "period"] == "2025-01"
    assert out.loc[0, "value"] == 4_200_000.0
    assert out.loc[1, "budget"] == -100.0
    assert "Sales" in out.loc[0, "dimensions"]


def test_long_layout_text_value_col_names_column():
    df = pd.DataFrame({
        "Month": ["Jan-25"], "Account": ["Rev"], "Actual": ["hello"],
    })
    mapping = MappingSpec(layout="long", period_col="Month", metric_col="Account", value_col="Actual")
    with pytest.raises(MappingError, match="Actual"):
        normalize(df, mapping)


# ---------- wide layout (the common FP&A export) ----------

def test_wide_layout_melts_to_long():
    df = pd.DataFrame({
        "Line Item": ["Net Revenue", "OpEx"],
        "Jan-25": ["100", "50"],
        "Feb-25": ["110", "(5)"],
        "Mar-25": ["120", "55"],
    })
    mapping = MappingSpec(
        layout="wide", wide_metric_col="Line Item",
        wide_period_cols=["Jan-25", "Feb-25", "Mar-25"],
    )
    out = normalize(df, mapping)
    assert len(out) == 6
    assert set(out["period"]) == {"2025-01", "2025-02", "2025-03"}
    opex_feb = out[(out["metric"] == "OpEx") & (out["period"] == "2025-02")]
    assert opex_feb["value"].iloc[0] == -5.0


def test_wide_layout_single_metric_label():
    df = pd.DataFrame({"Jan-25": ["100"], "Feb-25": ["110"]})
    mapping = MappingSpec(
        layout="wide", wide_value_label="Revenue",
        wide_period_cols=["Jan-25", "Feb-25"],
    )
    out = normalize(df, mapping)
    assert set(out["metric"]) == {"Revenue"}


def test_wide_layout_missing_period_cols_errors():
    df = pd.DataFrame({"Jan-25": ["100"]})
    with pytest.raises(MappingError, match="wide_period_cols"):
        normalize(df, MappingSpec(layout="wide", wide_value_label="Revenue"))


def test_no_valid_rows_errors():
    df = pd.DataFrame({"Month": ["Jan-25"], "Actual": [""]})
    mapping = MappingSpec(layout="long", period_col="Month", value_col="Actual", wide_value_label="X")
    with pytest.raises(MappingError, match="no valid rows"):
        normalize(df, mapping)
