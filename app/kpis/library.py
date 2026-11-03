"""Built-in KPI library (Addendum v1.1 Section 2.1): common FP&A KPIs the
user can pick to auto-fill category/unit/direction_good. Matching is fuzzy
on the source metric name so "Total Net Revenue" still suggests Net Revenue.

Order matters — the first match wins. Explicit cost lines ("Sales Salaries &
Commissions", "Sales Tools & CRM", "Cost of Revenue") must be claimed before
the bare "sales" revenue pattern at the bottom, or they default to
Revenue/up-is-good and every cost increase renders green.
"""

import re

# Cost-line words. Also used to stop the bare "sales" fallback from claiming
# "Sales Tools & CRM" or "Travel & Entertainment - Sales" as revenue.
_EXPENSE_NAME = re.compile(
    r"expense|\bcosts?\b|\bfees?\b|hosting|infrastructure|software|licen[cs]e|\btools?\b|"
    r"\brent\b|facilit|insurance|travel|\bt&e\b|contractor|depreciation|amorti[sz]ation|"
    r"bad debt|audit|legal|spend|\bsaas\b|benefits|severance|interest expense",
    re.I,
)

KPI_LIBRARY = [
    {"name": "Gross Margin %", "match": r"gross ?margin ?%|gm ?%", "category": "Profitability", "unit": "%", "direction_good": "up"},
    {"name": "Gross Profit", "match": r"gross ?(profit|margin(?! ?%))", "category": "Profitability", "unit": "USD", "direction_good": "up"},
    {"name": "EBITDA", "match": r"ebitda", "category": "Profitability", "unit": "USD", "direction_good": "up"},
    {"name": "Churned ARR", "match": r"churn", "category": "Retention", "unit": "USD", "direction_good": "down"},
    {"name": "Cost of Revenue", "match": r"cost of (revenue|sales|goods)|\bcogs\b", "category": "Cost", "unit": "USD", "direction_good": "down"},
    {"name": "Revenue", "match": r"\brevenue\b|turnover", "category": "Revenue", "unit": "USD", "direction_good": "up"},
    {"name": "Other Income", "match": r"\bincome\b", "category": "Other Income", "unit": "USD", "direction_good": "up"},
    {"name": "Headcount Cost", "match": r"headcount|payroll|people ?cost|salaries|commissions",
     "category": "Cost", "unit": "USD", "direction_good": "down"},
    {"name": "Marketing Spend", "match": r"marketing|s&m|advertis", "category": "Cost", "unit": "USD", "direction_good": "down"},
    {"name": "OpEx", "match": r"op(erating)? ?ex(pense)?s?|overheads", "category": "Cost", "unit": "USD", "direction_good": "down"},
    {"name": "Cash Runway", "match": r"cash ?(runway|balance)|runway", "category": "Balance Sheet", "unit": "USD", "direction_good": "up"},
    {"name": "New Bookings", "match": r"bookings|new ?(arr|business)", "category": "Sales", "unit": "USD", "direction_good": "up"},
    {"name": "Active Customers", "match": r"(active )?customers|logos|accounts", "category": "Growth", "unit": "count", "direction_good": "up"},
    {"name": "Revenue", "match": r"\bsales\b(?! ?&)", "unless": _EXPENSE_NAME, "category": "Revenue", "unit": "USD", "direction_good": "up"},
]


def library_listing() -> list[dict]:
    """The library as shown to users: each KPI once, without matcher internals.
    (A KPI can appear twice when it needs a late, lower-priority pattern.)"""
    seen: set[str] = set()
    return [{k: v for k, v in kpi.items() if k not in ("match", "unless")} for kpi in KPI_LIBRARY
            if not (kpi["name"] in seen or seen.add(kpi["name"]))]


def suggest_kpi(source_metric: str) -> dict | None:
    """Best library match for a source metric name, or None."""
    for kpi in KPI_LIBRARY:
        if re.search(kpi["match"], source_metric, re.I):
            if kpi.get("unless") is not None and kpi["unless"].search(source_metric):
                continue
            return {k: v for k, v in kpi.items() if k not in ("match", "unless")}
    return None


# ---- import-time defaults (KPI picker) ------------------------------------
# Unlike suggest_kpi, these always return category/unit/direction so a metric
# the library doesn't know still gets a sensible default instead of the UI's
# "Uncategorized · USD · up is good".

# Financial-statement section labels as they appear in an account_group-style
# column. "Sales & Marketing" is a cost section, not revenue.
_REVENUE_GROUP = re.compile(r"^\s*(revenue|sales|income|other income|turnover)\b(?!\s*&)", re.I)
_COST_GROUP = re.compile(
    r"cogs|cost|expense|opex|operating|research|r&d|development|general|g&a|"
    r"administrative|marketing|s&m|payroll|personnel",
    re.I,
)

_PERCENT_UNIT = re.compile(r"%|percent|\bpct\b|_pct|\brate\b|_rate|ratio", re.I)
_USD_UNIT = re.compile(r"usd|\$|revenue|\bcost|spend|\barr\b|_arr|amount|expense|fees|salar|income", re.I)
_COUNT_UNIT = re.compile(
    r"clicks|impressions|\bmqls?\b|\bsqls?\b|\bsqos?\b|opportunit|closed_won|\bwon\b|leads|visits|"
    r"sessions|signups|users|customers|logos|deals|\bcount\b|units|orders|tickets|headcount$",
    re.I,
)


def infer_unit(source_metric: str) -> str | None:
    """Unit from the measure name. An explicit currency suffix wins
    ("closed_won_arr_usd" is money even though it says "won")."""
    if re.search(r"usd|\$", source_metric, re.I):
        return "USD"
    if _PERCENT_UNIT.search(source_metric):
        return "%"
    if _COUNT_UNIT.search(source_metric):
        return "count"
    if _USD_UNIT.search(source_metric):
        return "USD"
    return None


def _hint_from_group(group: str | None) -> tuple[str, str] | None:
    if not group:
        return None
    if _REVENUE_GROUP.search(group):
        return ("Revenue", "up")
    if _COST_GROUP.search(group):
        return ("Cost", "down")
    return None


def _hint_from_code(code: str | None) -> tuple[str, str] | None:
    """Conventional 4-digit chart of accounts: 4xxx revenue, 5xxx COGS,
    6xxx opex. Other ranges (7xxx other income/expense) are ambiguous."""
    digits = re.sub(r"\D", "", str(code or ""))
    if len(digits) != 4:
        return None
    return {"4": ("Revenue", "up"), "5": ("Cost", "down"), "6": ("Cost", "down")}.get(digits[0])


def suggest_import_defaults(source_metric: str, group: str | None = None,
                            code: str | None = None) -> dict:
    """KPI-picker defaults for one imported metric. `group` is the file's own
    account-group/section value and `code` its account code — both outrank
    name heuristics because they come from the customer's ledger."""
    lib = suggest_kpi(source_metric)
    out: dict = {}
    if lib:
        out.update(lib)
    hint = _hint_from_group(group) or (None if lib else _hint_from_code(code))
    if hint is None and not lib and _EXPENSE_NAME.search(source_metric):
        hint = ("Cost", "down")
    if hint:
        if lib and (lib["category"], lib["direction_good"]) != hint:
            out.pop("name", None)   # the "→ <KPI>" label would contradict the ledger
        out["category"], out["direction_good"] = hint
    out.setdefault("category", "Uncategorized")
    out.setdefault("direction_good", "up")
    # A library unit other than the USD default is deliberate (Gross Margin %,
    # Active Customers); otherwise the measure's own name decides.
    if not (lib and lib["unit"] != "USD"):
        out["unit"] = infer_unit(source_metric) or "USD"
    return out
