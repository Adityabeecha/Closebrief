"""Period aggregation (v3.0): roll monthly metric values up to quarters and
years with per-KPI rules, deterministically.

Aggregation types:
  - flow    : additive over time (Revenue, COGS, OpEx, EBITDA, Bookings) -> SUM
  - balance : a point-in-time stock (Cash Balance) -> LAST month in the period
  - ratio   : a percentage/ratio (Gross Margin %, Churn %) -> cannot be summed or
              averaged; recompute from components if available, else UNAVAILABLE
              at coarser granularity (never average percentages).

A period key is "YYYY-MM" (month), "YYYY-Qn" (quarter), or "YYYY" (year).
"""

from __future__ import annotations

GRANULARITIES = ("month", "quarter", "year")

# Name heuristics for inferring the aggregation type when the KPI library
# doesn't pin one. Ratio is detected primarily by unit == "%".
_BALANCE_RE = ("cash", "balance", "runway", "headcount", "arr", "active customers", "customers", "logos")


def infer_aggregation_type(name: str, unit: str) -> str:
    u = (unit or "").strip().lower()
    if u in ("%", "percent", "ratio", "x"):
        return "ratio"
    n = (name or "").lower()
    if any(k in n for k in _BALANCE_RE):
        return "balance"
    return "flow"


def period_key(month: str, granularity: str) -> str:
    """Map a "YYYY-MM" month to its period key at the given granularity."""
    y, m = month.split("-")[:2]
    if granularity == "month":
        return f"{y}-{m}"
    if granularity == "quarter":
        q = (int(m) - 1) // 3 + 1
        return f"{y}-Q{q}"
    if granularity == "year":
        return y
    raise ValueError(f"Unknown granularity: {granularity}")


def sort_key(pkey: str) -> tuple:
    """Chronological sort key for any period key form."""
    if "-Q" in pkey:
        y, q = pkey.split("-Q")
        return (int(y), int(q))
    if "-" in pkey:
        y, m = pkey.split("-")[:2]
        return (int(y), int(m))
    return (int(pkey), 0)


def aggregate_series(rows: list[dict], granularity: str, agg_type: str) -> dict[str, dict]:
    """Aggregate one metric's monthly rows into period buckets.

    `rows`: [{"period": "YYYY-MM", "value": float, "budget": float|None}, ...]
    Returns {period_key: {"value": float|None, "budget": float|None,
                          "unavailable": bool, "months": int}}.
    """
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(period_key(r["period"], granularity), []).append(r)

    out: dict[str, dict] = {}
    for pk, items in buckets.items():
        items.sort(key=lambda r: r["period"])
        out[pk] = _reduce(items, agg_type)
        out[pk]["months"] = len(items)
    return out


def _reduce(items: list[dict], agg_type: str) -> dict:
    if agg_type == "ratio":
        # A percentage cannot be summed or averaged into a meaningful period
        # figure without its numerator/denominator, which we don't store
        # generically — so it is honestly unavailable at coarser granularity.
        if len(items) == 1:
            it = items[0]
            return {"value": it["value"], "budget": it.get("budget"), "unavailable": False}
        return {"value": None, "budget": None, "unavailable": True}

    if agg_type == "balance":
        last = items[-1]  # already sorted ascending by period
        return {"value": last["value"], "budget": last.get("budget"), "unavailable": False}

    # flow (default): additive
    vals = [i["value"] for i in items if i["value"] is not None]
    buds = [i["budget"] for i in items if i.get("budget") is not None]
    return {
        "value": sum(vals) if vals else None,
        "budget": sum(buds) if buds else None,
        "unavailable": False,
    }


def prior_key(pkey: str, granularity: str) -> str | None:
    """The immediately-preceding period key (for QoQ / MoM-equivalent deltas)."""
    if granularity == "month":
        y, m = int(pkey[:4]), int(pkey[5:7])
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        return f"{y}-{m:02d}"
    if granularity == "quarter":
        y, q = pkey.split("-Q")
        y, q = int(y), int(q) - 1
        if q == 0:
            y, q = y - 1, 4
        return f"{y}-Q{q}"
    if granularity == "year":
        return str(int(pkey) - 1)
    return None


def year_ago_key(pkey: str, granularity: str) -> str | None:
    """The same period one year earlier (for YoY deltas)."""
    if granularity == "month":
        return f"{int(pkey[:4]) - 1}-{pkey[5:7]}"
    if granularity == "quarter":
        y, q = pkey.split("-Q")
        return f"{int(y) - 1}-Q{q}"
    if granularity == "year":
        return str(int(pkey) - 1)
    return None
