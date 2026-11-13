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
# Words that make a KPI a monthly movement even when it also names a balance.
_FLOW_RE = ("cost", "expense", "spend", "payroll", "salar", "churn", "new ", "added", "lost",
            "gained", "bookings", "revenue", "hires", "won", "net change")


def infer_aggregation_type(name: str, unit: str) -> str:
    u = (unit or "").strip().lower()
    if u in ("%", "percent", "ratio", "x", "days"):
        return "ratio"
    n = (name or "").lower()
    if any(k in n for k in _FLOW_RE):
        return "flow"
    if any(k in n for k in _BALANCE_RE):
        return "balance"
    return "flow"


def period_key(month: str, granularity: str, fiscal_start: int = 1) -> str:
    """Map a "YYYY-MM" month to its period key at the given granularity."""
    y, m = month.split("-")[:2]
    if granularity == "month":
        return f"{y}-{m}"
    fiscal_year = int(y) + int(fiscal_start != 1 and int(m) >= fiscal_start)
    if granularity == "quarter":
        q = ((int(m) - fiscal_start) % 12) // 3 + 1
        return f"{fiscal_year:04d}-Q{q}"
    if granularity == "year":
        return f"{fiscal_year:04d}"
    raise ValueError(f"Unknown granularity: {granularity}")


def months_in(pkey: str, granularity: str, fiscal_start: int = 1) -> list[str]:
    """Calendar months covered by a key named for its fiscal ending year."""
    if granularity == "month":
        return [pkey]
    year = int(pkey[:4]) - int(fiscal_start != 1)
    offset = fiscal_start - 1
    if granularity == "quarter":
        offset += (int(pkey.split("-Q")[1]) - 1) * 3
        count = 3
    elif granularity == "year":
        count = 12
    else:
        raise ValueError(f"Unknown granularity: {granularity}")
    return [f"{(year * 12 + offset + i) // 12:04d}-{(offset + i) % 12 + 1:02d}" for i in range(count)]


def sort_key(pkey: str, fiscal_start: int = 1) -> tuple:
    """Chronological sort key for any period key form."""
    if fiscal_start != 1 and ("-Q" in pkey or "-" not in pkey):
        first = months_in(pkey, "quarter" if "-Q" in pkey else "year", fiscal_start)[0]
        return tuple(map(int, first.split("-")))
    if "-Q" in pkey:
        y, q = pkey.split("-Q")
        return (int(y), int(q))
    if "-" in pkey:
        y, m = pkey.split("-")[:2]
        return (int(y), int(m))
    return (int(pkey), 0)


def aggregate_series(rows: list[dict], granularity: str, agg_type: str, fiscal_start: int = 1) -> dict[str, dict]:
    """Aggregate one metric's monthly rows into period buckets.

    `rows`: [{"period": "YYYY-MM", "value": float, "budget": float|None}, ...]
    Returns {period_key: {"value": float|None, "budget": float|None,
                          "unavailable": bool, "months": int}}.
    """
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(period_key(r["period"], granularity, fiscal_start), []).append(r)

    out: dict[str, dict] = {}
    for pk, items in buckets.items():
        items.sort(key=lambda r: r["period"])
        out[pk] = _reduce(items, agg_type)
        out[pk]["months"] = len(items)
        if any("forecast" in i for i in items):
            forecasts = [i["forecast"] for i in items if i.get("forecast") is not None]
            out[pk]["forecast"] = (None if out[pk]["unavailable"] else items[-1].get("forecast")
                                   if agg_type == "balance" or agg_type == "ratio"
                                   else sum(forecasts) if forecasts else None)
    return out


def like_for_like(rows: list[dict], period: str, other: str, granularity: str, agg_type: str,
                  fiscal_start: int = 1) -> float | None:
    """The other period's value over the same months as `period` has data for, so a
    year with three months is compared with the same three months, not a whole year.
    None when any of those months is missing."""
    mine = months_in(period, granularity, fiscal_start)
    theirs = months_in(other, granularity, fiscal_start)
    present = {r["period"] for r in rows if r["period"] in set(mine)}
    picks = [theirs[i] for i, m in enumerate(mine) if m in present and i < len(theirs)]
    by_month = {r["period"]: r for r in rows}
    if not picks or any(m not in by_month for m in picks):
        return None
    reduced = _reduce(sorted((by_month[m] for m in picks), key=lambda r: r["period"]), agg_type)
    return None if reduced["unavailable"] else reduced["value"]


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


def prior_key(pkey: str, granularity: str, fiscal_start: int = 1) -> str | None:
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


def year_ago_key(pkey: str, granularity: str, fiscal_start: int = 1) -> str | None:
    """The same period one year earlier (for YoY deltas)."""
    if granularity == "month":
        return f"{int(pkey[:4]) - 1}-{pkey[5:7]}"
    if granularity == "quarter":
        y, q = pkey.split("-Q")
        return f"{int(y) - 1}-Q{q}"
    if granularity == "year":
        return str(int(pkey) - 1)
    return None
