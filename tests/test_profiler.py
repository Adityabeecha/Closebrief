import pandas as pd

from app.ingestion.profiler import is_period_name, profile_columns


def roles(profile):
    return {c["column_name"]: c["guessed_role"] for c in profile["columns"]}


def test_long_layout_role_guessing():
    df = pd.DataFrame({
        "Month": ["2025-01", "2025-02", "2025-03"],
        "Account": ["Revenue", "OpEx", "Revenue"],
        "Actual": ["100", "200", "300"],
        "Budget": ["90", "210", "310"],
        "Department": ["Sales", "Ops", "Sales"],
        "Notes": ["long free text about things", "even more different text", "third unique note here"],
    })
    profile = profile_columns(df)
    r = roles(profile)
    assert profile["layout_guess"] == "long"
    assert r["Month"] == "period"
    assert r["Account"] == "metric_label"
    assert r["Actual"] == "measure"
    assert r["Budget"] == "budget"
    assert r["Department"] == "dimension"


def test_wide_layout_detection():
    df = pd.DataFrame({
        "Line Item": ["Revenue", "OpEx"],
        "Jan-25": ["1", "2"], "Feb-25": ["3", "4"], "Mar-25": ["5", "6"], "Apr-25": ["7", "8"],
    })
    profile = profile_columns(df)
    assert profile["layout_guess"] == "wide"
    assert set(profile["wide_period_cols"]) == {"Jan-25", "Feb-25", "Mar-25", "Apr-25"}


def test_period_shaped_names():
    for name in ["Jan-25", "2025-03", "Q1 2025", "FY24", "Mar 2025", "03/2025"]:
        assert is_period_name(name), name
    for name in ["Account", "Budget", "Notes", "Revenue"]:
        assert not is_period_name(name), name


def test_numeric_with_currency_symbols_is_measure():
    df = pd.DataFrame({
        "Period": ["2025-01", "2025-02"],
        "Amount": ["$1,200.50", "($300)"],
    })
    r = roles(profile_columns(df))
    assert r["Amount"] == "measure"


def test_budget_name_variants():
    df = pd.DataFrame({
        "Period": ["2025-01"], "Val": ["10"],
        "Fcst": ["12"], "Target": ["11"],
    })
    r = roles(profile_columns(df))
    assert r["Fcst"] == "budget"
    assert r["Target"] == "budget"
