"""SEC KPI count/ARR defaults and filing-note conflict regressions."""

import pytest

from app.context.conflicts import find_conflicts
from app.kpis.library import infer_unit, suggest_import_defaults, suggest_kpi
from app.schemas import ContextDoc


@pytest.mark.parametrize("name,agg", [("Ending ARR", "balance"), ("Total ARR", "balance"),
                                      ("ARR (USD)", "balance"), ("Net New ARR", "flow")])
def test_arr_library_variants(name, agg):
    d = suggest_import_defaults(name)
    assert suggest_kpi(name)["unit"] == "USD"
    assert (d["unit"], d["direction_good"], d["aggregation_type"]) == ("USD", "up", agg)


@pytest.mark.parametrize("name", ["Customers >$1M Product Revenue", "Customers >$100K ARR",
                                  "Enterprise Customers >$100K ARR", "Customers with ARR over $1M",
                                  "Logos with Revenue", "Accounts >$100K ARR", "Users with Revenue",
                                  "Employees with Revenue", "Headcount with Revenue", "# of Revenue Accounts",
                                  "Number of Customers with ARR"])
def test_count_names_override_money_words(name):
    d = suggest_import_defaults(name)
    assert infer_unit(name) == "count"
    assert suggest_kpi(name)["unit"] == "count"
    assert (d["unit"], d["aggregation_type"], d["direction_good"], d["category"]) == ("count", "balance", "up", "Growth")


def docs(a, b):
    return [ContextDoc(id=i, title="Filing", body=body, metric_tags=["NRR"])
            for i, body in enumerate((a, b), 1)]


@pytest.mark.parametrize("a,b", [
    ("Snowflake product revenue grew 4 percent with enterprise customers expanding.",
     "Snowflake product revenue grew with 7 enterprise customers expanding."),
    ("GitLab revenue operating margin was -38.59% in FY2026.",
     "GitLab revenue operating margin included $48.9 in FY2027 (figures in millions)."),
    ("Product revenue was $297551000. Enterprise restructuring charges were disclosed separately.",
     "Product revenue continued growing rapidly throughout the period. Enterprise restructuring charges were $30000000."),
    ("Enterprise revenue customer count was 4 in Q1 FY27.", "Enterprise revenue customer count was 7 in Q2 FY28."),
    ("Enterprise revenue customer count was 2025.", "Enterprise revenue customer count was 2026."),
])
def test_filing_numbers_of_different_kinds_or_subjects_do_not_conflict(a, b):
    assert find_conflicts(docs(a, b)) == []


@pytest.mark.parametrize("a,b", [("$612K", "$580K"), ("612K ARR", "580K ARR"), ("48.9%", "45%"), ("$61", "$58"),
                                ("USD 612K", "USD 580K"), ("$ 612K", "$ 580K")])
def test_same_subject_same_kind_conflicts_survive(a, b):
    assert find_conflicts(docs(f"Enterprise churn from pricing change was {a}.",
                               f"Enterprise churn from pricing change was {b}."))


@pytest.mark.parametrize("other", ["$600K", "$612K", "$6.12M", "$7M"])
def test_conflicts_keep_rounding_and_tenfold_ratio_limits(other):
    assert find_conflicts(docs("Enterprise churn from pricing change was $612K.",
                               f"Enterprise churn from pricing change was {other}.")) == []


