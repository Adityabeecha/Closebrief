"""Built-in KPI library (Addendum v1.1 Section 2.1): common FP&A KPIs the
user can pick to auto-fill category/unit/direction_good. Matching is fuzzy
on the source metric name so "Total Net Revenue" still suggests Net Revenue.
"""

import re

KPI_LIBRARY = [
    {"name": "Revenue", "match": r"revenue|sales(?! &)|turnover", "category": "Revenue", "unit": "USD", "direction_good": "up"},
    {"name": "Gross Profit", "match": r"gross ?(profit|margin(?! ?%))", "category": "Profitability", "unit": "USD", "direction_good": "up"},
    {"name": "Gross Margin %", "match": r"gross ?margin ?%|gm ?%", "category": "Profitability", "unit": "%", "direction_good": "up"},
    {"name": "OpEx", "match": r"op(erating)? ?ex(pense)?s?|overheads", "category": "Cost", "unit": "USD", "direction_good": "down"},
    {"name": "EBITDA", "match": r"ebitda", "category": "Profitability", "unit": "USD", "direction_good": "up"},
    {"name": "Cash Runway", "match": r"cash ?(runway|balance)|runway", "category": "Balance Sheet", "unit": "USD", "direction_good": "up"},
    {"name": "New Bookings", "match": r"bookings|new ?(arr|business)", "category": "Sales", "unit": "USD", "direction_good": "up"},
    {"name": "Churned ARR", "match": r"churn", "category": "Retention", "unit": "USD", "direction_good": "down"},
    {"name": "Headcount Cost", "match": r"headcount|payroll|people ?cost|salaries", "category": "Cost", "unit": "USD", "direction_good": "down"},
    {"name": "Marketing Spend", "match": r"marketing|s&m|advertis", "category": "Cost", "unit": "USD", "direction_good": "down"},
    {"name": "Active Customers", "match": r"(active )?customers|logos|accounts", "category": "Growth", "unit": "count", "direction_good": "up"},
]


def suggest_kpi(source_metric: str) -> dict | None:
    """Best library match for a source metric name, or None."""
    for kpi in KPI_LIBRARY:
        if re.search(kpi["match"], source_metric, re.I):
            return {k: v for k, v in kpi.items() if k != "match"}
    return None
