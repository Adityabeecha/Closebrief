"""Cross-domain insights (v5.0). Auto-correlate metrics across DIFFERENT datasets
in a workspace (e.g. a Marketing dataset's spend vs an FP&A dataset's revenue),
including a lag scan so leading indicators surface ("spend → revenue lift N
months later"). Deterministic — reuses the Pearson machinery; the LLM never
computes these.
"""

from __future__ import annotations

from app.compute.correlations import CORR_THRESHOLD, MIN_OVERLAP, _month_key, _pearson


def _add_months(period: str, n: int) -> str:
    y, m = _month_key(period)
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _load_series(conn, dataset_ids: list[int]) -> list[dict]:
    series = []
    for ds in dataset_ids:
        drow = conn.execute("SELECT name, domain FROM datasets WHERE id = ?", (ds,)).fetchone()
        rows = conn.execute(
            """SELECT m.name AS metric, mv.period AS period, mv.value AS value
               FROM metrics m JOIN metric_values mv ON mv.metric_id = m.id
               WHERE m.dataset_id = ? ORDER BY m.name, mv.period""",
            (ds,),
        ).fetchall()
        by_metric: dict[str, dict] = {}
        for r in rows:
            by_metric.setdefault(r["metric"], {}).setdefault("values", {})[r["period"]] = float(r["value"])
        for metric, d in by_metric.items():
            series.append({"dataset_id": ds,
                           "dataset_name": (drow["name"] if drow else str(ds)),
                           "domain": (drow["domain"] if drow else "fpa"),
                           "metric": metric, "values": d["values"]})
    return series


def cross_domain_correlations(conn, dataset_ids: list[int], *,
                              threshold: float = CORR_THRESHOLD,
                              min_overlap: int = MIN_OVERLAP, max_lag: int = 3) -> list[dict]:
    """Strong correlations between metrics in *different* datasets. For each pair,
    the lag (0..max_lag) with the strongest |r| is kept, so a leading relationship
    (A leads B by `lag` months) is reported."""
    series = _load_series(conn, dataset_ids)
    out = []
    for i, a in enumerate(series):
        for b in series[i + 1:]:
            if a["dataset_id"] == b["dataset_id"]:
                continue   # cross-DATASET only
            best = None
            for lag in range(0, max_lag + 1):
                xs, ys = [], []
                for period, av in a["values"].items():
                    bv = b["values"].get(_add_months(period, lag))
                    if bv is not None:
                        xs.append(av)
                        ys.append(bv)
                if len(xs) < min_overlap:
                    continue
                r = _pearson(xs, ys)
                if r is None or abs(r) < threshold:
                    continue
                if best is None or abs(r) > abs(best["r"]):
                    best = {"r": round(r, 2), "lag": lag, "months": len(xs)}
            if best:
                out.append({
                    "metric_a": a["metric"], "dataset_a": a["dataset_name"], "domain_a": a["domain"],
                    "metric_b": b["metric"], "dataset_b": b["dataset_name"], "domain_b": b["domain"],
                    "direction": "positive" if best["r"] > 0 else "negative",
                    **best,
                })
    return sorted(out, key=lambda p: -abs(p["r"]))
