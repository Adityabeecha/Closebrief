"""KPI-picker defaults (QA_REVIEW_2026-10.md P0 #5 direction/category, #6 unit).

Before: every unknown GL line defaulted to "Uncategorized · USD · up is good",
anything containing "Sales" was Revenue, and funnel counts showed as dollars.
"""

import pytest
from test_ingestion_acceptance import _confirm, _schema, _upload, client  # noqa: F401 - fixture

from app.kpis.library import infer_unit, library_listing, suggest_import_defaults, suggest_kpi


@pytest.mark.parametrize("name", [
    "Bad Debt Expense", "Cloud Hosting & Infrastructure", "Legal & Professional Fees",
    "Insurance", "Payment Processing Fees", "Depreciation & Amortization",
    "Sales Tools & CRM", "Travel & Entertainment - Sales", "Sales Salaries & Commissions",
    "Cost of Revenue",
])
def test_cost_lines_default_to_cost_down_by_name(name):
    s = suggest_import_defaults(name)
    assert (s["category"], s["direction_good"]) == ("Cost", "down"), s
    assert s.get("name") != "Revenue"


@pytest.mark.parametrize("name", ["Subscription Revenue", "Net Sales", "Sales", "Net Revenue"])
def test_revenue_lines_stay_revenue_up(name):
    s = suggest_import_defaults(name)
    assert (s["category"], s["direction_good"]) == ("Revenue", "up"), s


def test_ledger_account_group_outranks_name_keywords():
    # The file's own classification wins over a misleading name.
    s = suggest_import_defaults("Sales Kickoff", group="Sales & Marketing")
    assert (s["category"], s["direction_good"]) == ("Cost", "down")
    assert suggest_import_defaults("Widget Income", group="Revenue")["direction_good"] == "up"
    # An uninformative group ("Other") falls back to the name.
    assert suggest_import_defaults("Interest Income", group="Other")["direction_good"] == "up"


def test_account_code_range_used_when_name_is_ambiguous():
    assert suggest_import_defaults("Account 5010", code="5010")["direction_good"] == "down"
    assert suggest_import_defaults("Account 4010", code="4010")["category"] == "Revenue"
    # 7xxx is other income OR expense — no guess.
    assert suggest_import_defaults("Account 7020", code="7020")["category"] == "Uncategorized"


@pytest.mark.parametrize("name,unit", [
    ("Paid Search clicks", "count"), ("Webinars mqls", "count"), ("Webinars sqls", "count"),
    ("Content/SEO closed_won", "count"), ("Content/SEO sq_opportunities", "count"),
    ("Content/SEO closed_won_arr_usd", "USD"), ("Webinars spend_usd", "USD"),
    ("Gross Margin %", "%"), ("Win rate", "%"), ("Subscription Revenue", "USD"),
])
def test_unit_inferred_from_measure_name(name, unit):
    assert suggest_import_defaults(name)["unit"] == unit
    assert infer_unit(name) == unit


def test_demo_library_picks_unchanged():
    # demo.py curates the sample dataset via suggest_kpi; reordering the
    # library must not change what it picks.
    expected = {
        "Net Revenue": "Revenue", "Headcount Cost": "Headcount Cost",
        "Sales & Marketing Spend": "Marketing Spend", "Operating Expenses": "OpEx",
        "Gross Profit": "Gross Profit", "Churned ARR": "Churned ARR",
        "New Bookings": "New Bookings", "Cash Balance": "Cash Runway",
        "Active Customers": "Active Customers", "EBITDA": "EBITDA",
    }
    assert {n: (suggest_kpi(n) or {}).get("name") for n in expected} == expected


def test_library_listing_lists_each_kpi_once_without_internals():
    listing = library_listing()
    names = [k["name"] for k in listing]
    assert len(names) == len(set(names))
    assert all("match" not in k and "unless" not in k for k in listing)


def test_gl_import_picker_uses_account_group(client):  # noqa: F811
    up = _upload(client, "finance_gl_actuals.csv", "text/csv")
    _confirm(client, up["upload_id"], _schema(client, up["upload_id"])["suggested_mapping"])
    rows = client.get(f"/ingest/{up['upload_id']}/metrics").json()
    s = {r["source_metric"]: r["suggestion"] for r in rows}
    assert len(s) == 27
    for cost in ("Bad Debt Expense", "Cloud Hosting & Infrastructure", "Sales Salaries & Commissions",
                 "Sales Tools & CRM", "Travel & Entertainment - Sales", "Legal & Professional Fees"):
        assert (s[cost]["category"], s[cost]["direction_good"]) == ("Cost", "down"), (cost, s[cost])
    assert (s["Subscription Revenue"]["category"], s["Subscription Revenue"]["direction_good"]) == ("Revenue", "up")
    assert all(v["unit"] == "USD" for v in s.values())


def test_funnel_import_picker_counts_are_not_dollars(client):  # noqa: F811
    up = _upload(client, "marketing_funnel_monthly.csv", "text/csv")
    _confirm(client, up["upload_id"], _schema(client, up["upload_id"])["suggested_mapping"])
    s = {r["source_metric"]: r["suggestion"] for r in client.get(f"/ingest/{up['upload_id']}/metrics").json()}
    counts = [m for m in s if m.endswith((" mqls", " sqls", " closed_won", " clicks"))]
    assert counts, sorted(s)[:10]
    assert all(s[m]["unit"] == "count" for m in counts), {m: s[m]["unit"] for m in counts}
    assert all(s[m]["unit"] == "USD" for m in s if m.endswith("_usd"))


def test_unrecognised_kpis_say_their_direction_is_a_guess():
    # F20: the picker asks "which way is good?" instead of silently choosing "up",
    # which paints every rise in an unrecognised cost green.
    assert suggest_import_defaults("Warehouse handling")["direction_known"] is False
    for known in ("Net Revenue", "Operating Expenses", "Cloud Hosting", "Sales Kickoff"):
        group = "Sales & Marketing" if known == "Sales Kickoff" else None
        assert suggest_import_defaults(known, group=group)["direction_known"] is True, known
