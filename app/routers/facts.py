"""HTTP routes moved from app.main: facts."""

import json

from fastapi import APIRouter, Depends, HTTPException

from app.api import require_read, require_write  # noqa: E402
from app.auth import CurrentUser
from app.compute.aggregate import (
    GRANULARITIES,
    aggregate_series,
    infer_aggregation_type,
    prior_key,
    sort_key,
    year_ago_key,
)
from app.compute.availability import has_usable_value
from app.compute.correlations import (
    compute_correlations,
    correlations_for_metric,
    streak_from_history,
)
from app.compute.funnel import compute_funnel
from app.compute.pvm import bridge_for_metric_row
from app.datasets import (
    _scope_pred,
    active_dataset_id,
    dataset_currency,
)
from app.db import get_connection
from app.domains import get_domain, registry
from app.grounding import attribute
from app.routes_common import _active_domain_slug
from app.schemas import (
    ComputedFact,
    ContextSnippet,
    Deltas,
)

router = APIRouter()


@router.get("/correlations")
def correlations_endpoint(
    period: str | None = None, metric: str | None = None,
    _: CurrentUser = Depends(require_read),
) -> list[dict]:
    """Metric pairs that move together over time (|Pearson r| >= 0.7) in the
    active dataset. If `metric` is given, only pairs involving it (phrased with
    that metric first)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return []
        if metric:
            return correlations_for_metric(conn, ds, metric, period)
        return compute_correlations(conn, ds, period)
    finally:
        conn.close()


@router.get("/domain")
def get_active_domain(_: CurrentUser = Depends(require_read)) -> dict:
    """The active dataset's domain (defaults to fpa)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        slug = "fpa"
        if ds is not None:
            row = conn.execute("SELECT domain FROM datasets WHERE id = ?", (ds,)).fetchone()
            if row and row["domain"]:
                slug = row["domain"]
        return {**get_domain(slug).public(), "currency": dataset_currency(conn, ds)}
    finally:
        conn.close()


@router.put("/domain")
def set_active_domain(payload: dict, _: CurrentUser = Depends(require_write)) -> dict:
    slug = payload.get("domain")
    if not registry.has(slug or ""):
        raise HTTPException(status_code=422, detail=f"Unknown domain: {slug}")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=400, detail="No active dataset")
        conn.execute("UPDATE datasets SET domain = ? WHERE id = ?", (slug, ds))
        conn.commit()
        return {**get_domain(slug).public(), "currency": dataset_currency(conn, ds)}
    finally:
        conn.close()


@router.get("/funnel")
def get_funnel(period: str, _: CurrentUser = Depends(require_read)) -> dict:
    """Stage-over-stage funnel for the active dataset, if its domain defines one
    (Phase 1). Deterministic — the LLM is not involved."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=400, detail="No active dataset")
        domain = get_domain(_active_domain_slug(conn))
        if not domain.funnel:
            raise HTTPException(status_code=400,
                                detail=f"The {domain.name} domain has no funnel defined")
        return compute_funnel(conn, ds, period, domain.funnel)
    finally:
        conn.close()


def _chart_data(conn, metric_id: int, metric_name: str, period: str) -> dict:
    """chart_data block (Addendum v1.1 Section 4): 12-month trend, budget vs
    actual, and the P/V/M variance bridge (null when no qty/price detail)."""
    trend_rows = conn.execute(
        """
        SELECT period, value, budget FROM metric_values
        WHERE metric_id = ? AND period <= ?
        ORDER BY period DESC LIMIT 12
        """,
        (metric_id, period),
    ).fetchall()
    current = trend_rows[0] if trend_rows and trend_rows[0]["period"] == period else None
    return _chart_from_rows(trend_rows, current, _load_bridge(conn, metric_name, period))


def _chart_from_rows(history_desc, current, vb: dict | None) -> dict:
    """Build chart_data from a metric's metric_values rows (newest first) and its
    already-resolved P/V/M bridge."""
    trend = [
        {"period": r["period"], "value": r["value"], "budget": r["budget"]}
        for r in reversed(history_desc[:12])
    ]
    budget_vs_actual = (
        {"actual": current["value"], "budget": current["budget"]} if current else None
    )
    bridge = None
    if vb is not None:
        bridge = [
            {"component": "Volume", "impact": vb["volume"]},
            {"component": "Price", "impact": vb["price"]},
            {"component": "Mix", "impact": vb["mix"]},
        ]
    return {"trend": trend, "budget_vs_actual": budget_vs_actual, "variance_bridge": bridge}


def _prefetch_cards(conn, names_by_id: dict[int, str], period: str) -> dict[int, tuple[dict, dict | None]]:
    """chart_data and trend streak for every dashboard card in three queries.
    Per card this was ~7 round trips (trend, current, bridge x3, streak x2),
    which on a remote Postgres made /facts take ~9 s for 7 KPIs."""
    if not names_by_id:
        return {}
    ids = list(names_by_id)
    ph = ",".join("?" * len(ids))
    history: dict[int, list] = {}
    for r in conn.execute(
        f"""SELECT metric_id, period, value, budget, quantity, budget_quantity, price, budget_price
            FROM metric_values WHERE metric_id IN ({ph}) AND period <= ?
            ORDER BY metric_id, period DESC""",
        (*ids, period),
    ).fetchall():
        history.setdefault(r["metric_id"], []).append(r)
    pvm = {
        r["metric_id"]: r
        for r in conn.execute(
            f"SELECT metric_id, volume, price, mix FROM pvm_bridges WHERE metric_id IN ({ph}) AND period = ?",
            (*ids, period),
        ).fetchall()
    }
    moms: dict[int, list] = {}
    for r in conn.execute(
        f"""SELECT metric_id, period, mom_pct FROM computed_facts
            WHERE metric_id IN ({ph}) AND mom_pct IS NOT NULL ORDER BY metric_id, period""",
        tuple(ids),
    ).fetchall():
        moms.setdefault(r["metric_id"], []).append((r["period"], r["mom_pct"]))
    out = {}
    for mid, name in names_by_id.items():
        h = history.get(mid, [])
        current = h[0] if h and h[0]["period"] == period else None
        out[mid] = (
            _chart_from_rows(h, current, _bridge_from_rows(pvm.get(mid), current)),
            streak_from_history(moms.get(mid, []), period, name),
        )
    return out


def _aggregated_facts(conn, ds: int, period: str, granularity: str, include_charts: bool = True) -> list[dict]:
    """Cards for a quarter/year period, aggregated from monthly metric_values
    per each KPI's aggregation rule (flow=SUM, balance=LAST, ratio=unavailable).
    Deltas are QoQ/YoY-equivalent; budget variance uses summed budgets."""
    kpis = conn.execute(
        """SELECT k.source_metric AS metric, k.display_name, k.category, k.unit,
                  k.direction_good, k.aggregation_type, m.id AS metric_id
           FROM kpi_configs k
           LEFT JOIN metrics m ON m.dataset_id = k.dataset_id AND m.name = k.source_metric
           WHERE k.dataset_id = ? ORDER BY k.source_metric""",
        (ds,),
    ).fetchall()
    if not kpis:
        kpis = conn.execute(
            """SELECT name AS metric, name AS display_name, category, unit, direction_good,
                      NULL AS aggregation_type, id AS metric_id
               FROM metrics WHERE dataset_id = ? ORDER BY name""",
            (ds,),
        ).fetchall()

    currency = dataset_currency(conn, ds)
    out = []
    prior = prior_key(period, granularity)
    yago = year_ago_key(period, granularity)
    for k in kpis:
        agg = k["aggregation_type"] or infer_aggregation_type(k["display_name"] or k["metric"], k["unit"])
        rows = []
        if k["metric_id"] is not None:
            rows = [
                {"period": r["period"], "value": r["value"], "budget": r["budget"]}
                for r in conn.execute(
                    "SELECT period, value, budget FROM metric_values WHERE metric_id = ?",
                    (k["metric_id"],),
                ).fetchall()
            ]
        series = aggregate_series(rows, granularity, agg)
        cur = series.get(period)
        has_data = cur is not None and not cur.get("unavailable") and cur.get("value") is not None
        deltas = {"mom_pct": None, "yoy_pct": None, "budget_var_abs": None, "budget_var_pct": None}
        trend = None
        if has_data:
            v = cur["value"]
            pv = (series.get(prior) or {}).get("value")
            yv = (series.get(yago) or {}).get("value")
            if pv:
                deltas["mom_pct"] = round((v - pv) / abs(pv) * 100, 2)
            if yv:
                deltas["yoy_pct"] = round((v - yv) / abs(yv) * 100, 2)
            if cur.get("budget") is not None:
                deltas["budget_var_abs"] = round(v - cur["budget"], 2)
                if cur["budget"]:
                    deltas["budget_var_pct"] = round((v - cur["budget"]) / abs(cur["budget"]) * 100, 2)
            trend = [
                {"period": pk, "value": series[pk]["value"], "budget": series[pk]["budget"]}
                for pk in sorted(series, key=sort_key) if not series[pk].get("unavailable")
            ]
        out.append({
            "metric": k["display_name"] or k["metric"], "category": k["category"],
            "period": period, "value": cur["value"] if cur else None, "unit": k["unit"], "currency": currency,
            "direction_good": k["direction_good"], "prior_value": None,
            "has_data": has_data, "deltas": deltas, "trend": None,
            "is_anomaly": False, "aggregated": True, "granularity": granularity,
            "aggregation_type": agg,
            "unavailable": bool(cur and cur.get("unavailable")),
            "report_id": None, "review_status": None, "assigned_email": None,
            "narrative": None, "sources": [],
            "confidence": None, "faithfulness": None,
            "chart_data": {"trend": trend} if (include_charts and trend) else None,
            "trend_streak": None,
        })
    # Movers first (largest absolute budget variance).
    out.sort(key=lambda f: abs(f["deltas"]["budget_var_abs"] or 0), reverse=True)
    return out


@router.get("/facts")
def list_facts(period: str, granularity: str = "month", include_charts: bool = True,
               user: CurrentUser = Depends(require_read)) -> list[dict]:
    """Dashboard cards for the ACTIVE dataset. Driven by the user's KPI
    selection (kpi_configs): shows exactly the selected KPIs, each LEFT JOINed
    to its computed fact for the period — so a selected KPI with no data for
    this period still renders an empty card (never silently hidden). If no KPIs
    were selected yet, falls back to every metric in the active dataset.

    granularity=quarter|year aggregates monthly values per each KPI's rule."""
    if granularity not in GRANULARITIES:
        raise HTTPException(status_code=422, detail=f"granularity must be one of {GRANULARITIES}")
    if granularity != "month":
        conn = get_connection()
        try:
            ds = active_dataset_id(conn)
            if ds is None:
                return []
            return _aggregated_facts(conn, ds, period, granularity, include_charts)
        finally:
            conn.close()
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return []

        currency = dataset_currency(conn, ds)
        has_kpis = conn.execute(
            "SELECT 1 FROM kpi_configs WHERE dataset_id = ? LIMIT 1", (ds,)
        ).fetchone() is not None

        if has_kpis:
            # Driver = selected KPIs; category/unit/direction come from the
            # KPI config, metric row is matched by name within the dataset.
            rows = conn.execute(
                """
                SELECT m.id AS metric_id, k.source_metric AS metric,
                       k.category, k.unit, k.direction_good,
                       cf.value, cf.prior_value, cf.mom_pct, cf.yoy_pct,
                       cf.budget_var_abs, cf.budget_var_pct, cf.trend, cf.is_anomaly
                FROM kpi_configs k
                LEFT JOIN metrics m ON m.dataset_id = k.dataset_id AND m.name = k.source_metric
                LEFT JOIN computed_facts cf ON cf.metric_id = m.id AND cf.period = ?
                WHERE k.dataset_id = ?
                ORDER BY ABS(COALESCE(cf.budget_var_abs, 0)) DESC, k.source_metric
                """,
                (period, ds),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.id AS metric_id, m.name AS metric, m.category, m.unit,
                       m.direction_good, cf.value, cf.prior_value, cf.mom_pct,
                       cf.yoy_pct, cf.budget_var_abs, cf.budget_var_pct, cf.trend, cf.is_anomaly
                FROM metrics m
                LEFT JOIN computed_facts cf ON cf.metric_id = m.id AND cf.period = ?
                WHERE m.dataset_id = ?
                ORDER BY ABS(COALESCE(cf.budget_var_abs, 0)) DESC, m.name
                """,
                (period, ds),
            ).fetchall()

        sensitive = user.role == "admin"   # gate cost/latency/emails to admins
        # Batch the per-card report + feedback lookups into two queries instead of
        # ~3 per card (was an N+1 over the dashboard's hot path).
        metric_ids = [r["metric_id"] for r in rows if r["metric_id"] is not None]
        reports_by_metric, edited_by_report, reviewer_by_report = {}, {}, {}
        if metric_ids:
            ph = ",".join("?" * len(metric_ids))
            for rr in conn.execute(
                f"""SELECT gr.id, gr.metric_id, gr.narrative, gr.sources, gr.confidence,
                           gr.faithfulness, gr.cost_usd, gr.latency_ms, gr.user_email,
                           gr.review_status, gr.assigned_email
                    FROM generated_reports gr
                    WHERE gr.metric_id IN ({ph}) AND gr.period = ?
                      AND (gr.prompt_version IS NULL OR gr.prompt_version NOT LIKE '%-digest')
                    ORDER BY gr.id ASC""",
                (*metric_ids, period),
            ).fetchall():
                reports_by_metric[rr["metric_id"]] = rr   # ASC → latest id wins
            report_ids = [rr["id"] for rr in reports_by_metric.values()]
            if report_ids:
                ph2 = ",".join("?" * len(report_ids))
                for fr in conn.execute(
                    f"""SELECT report_id, action, edited_text, user_email
                        FROM feedback WHERE report_id IN ({ph2}) ORDER BY id ASC""",
                    tuple(report_ids),
                ).fetchall():
                    if fr["action"] == "edited" and fr["edited_text"] is not None:
                        edited_by_report[fr["report_id"]] = fr["edited_text"]
                    if fr["user_email"] is not None:
                        reviewer_by_report[fr["report_id"]] = fr["user_email"]

        card_data = _prefetch_cards(
            conn,
            {r["metric_id"]: r["metric"] for r in rows if has_usable_value(r["metric_id"], r["value"])},
            period,
        )
        out = []
        for r in rows:
            has_data = has_usable_value(r["metric_id"], r["value"])
            report = reports_by_metric.get(r["metric_id"]) if has_data else None
            edited = edited_by_report.get(report["id"]) if report else None
            reviewer = reviewer_by_report.get(report["id"]) if report else None
            out.append({
                "metric": r["metric"],
                "category": r["category"],
                "period": period,
                "value": r["value"],
                "unit": r["unit"], "currency": currency,
                "direction_good": r["direction_good"],
                "prior_value": r["prior_value"],
                "has_data": has_data,
                "deltas": {
                    "mom_pct": r["mom_pct"], "yoy_pct": r["yoy_pct"],
                    "budget_var_abs": r["budget_var_abs"], "budget_var_pct": r["budget_var_pct"],
                },
                "trend": r["trend"],
                "is_anomaly": bool(r["is_anomaly"]) if r["is_anomaly"] is not None else False,
                "report_id": report["id"] if report else None,
                "review_status": report["review_status"] if report else None,
                "assigned_email": report["assigned_email"] if report else None,
                "narrative": (edited or report["narrative"]) if report else None,
                "original_narrative": report["narrative"] if (report and edited) else None,
                "edited": edited is not None,
                "sources": json.loads(report["sources"]) if (report and report["sources"]) else [],
                "confidence": report["confidence"] if report else None,
                "faithfulness": report["faithfulness"] if report else None,
                # Cost, latency and user emails are operational/PII detail — only
                # admins see them (matches /costs being admin-only).
                "cost_usd": report["cost_usd"] if (report and sensitive) else None,
                "latency_ms": report["latency_ms"] if (report and sensitive) else None,
                "generated_by": report["user_email"] if (report and sensitive) else None,
                "reviewed_by": reviewer if (report and sensitive) else None,
                "chart_data": card_data[r["metric_id"]][0] if include_charts and has_data else None,
                "trend_streak": card_data[r["metric_id"]][1] if has_data else None,
            })
        return out
    finally:
        conn.close()


def _active_metric_id(conn, metric: str) -> int | None:
    ds = active_dataset_id(conn)
    if ds is None:
        return None
    row = conn.execute(
        "SELECT id FROM metrics WHERE name = ? AND dataset_id = ?", (metric, ds)
    ).fetchone()
    return int(row["id"]) if row else None


def _load_bridge(conn, metric: str, period: str) -> dict | None:
    """Prefer the stored multi-item P/V/M bridge (from dimension-level detail);
    fall back to an on-the-fly single-row bridge for simple qty/price data.
    Scoped to the active dataset's metric."""
    metric_id = _active_metric_id(conn, metric)
    if metric_id is None:
        return None
    row = conn.execute(
        "SELECT volume, price, mix FROM pvm_bridges WHERE metric_id = ? AND period = ?",
        (metric_id, period),
    ).fetchone()
    if row is not None:
        return _bridge_from_rows(row, None)
    mv = conn.execute(
        """
        SELECT quantity, budget_quantity, price, budget_price
        FROM metric_values WHERE metric_id = ? AND period = ?
        """,
        (metric_id, period),
    ).fetchone()
    return _bridge_from_rows(None, mv)


def _bridge_from_rows(pvm_row, mv_row) -> dict | None:
    """Stored multi-item P/V/M bridge if there is one, else an on-the-fly
    single-row bridge from the period's qty/price detail."""
    if pvm_row is not None:
        return {"volume": pvm_row["volume"], "price": pvm_row["price"], "mix": pvm_row["mix"]}
    if mv_row:
        vb = bridge_for_metric_row(
            mv_row["quantity"], mv_row["budget_quantity"], mv_row["price"], mv_row["budget_price"]
        )
        if vb is not None:
            return {"volume": vb.volume, "price": vb.price, "mix": vb.mix}
    return None


def _fact_by_metric_id(conn, metric_id: int, period: str) -> ComputedFact | None:
    row = conn.execute(
        """
        SELECT m.name AS metric, m.category, m.unit, d.currency, cf.period, cf.value, cf.prior_value,
               cf.mom_pct, cf.yoy_pct, cf.budget_var_abs, cf.budget_var_pct, cf.trend, cf.is_anomaly
        FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
        LEFT JOIN datasets d ON d.id = m.dataset_id
        WHERE cf.metric_id = ? AND cf.period = ?
        """,
        (metric_id, period),
    ).fetchone()
    if row is None:
        return None
    return ComputedFact(
        metric=row["metric"], category=row["category"], period=row["period"],
        value=row["value"], unit=row["unit"], currency=row["currency"] or "USD", prior_value=row["prior_value"],
        deltas=Deltas(
            mom_pct=row["mom_pct"], yoy_pct=row["yoy_pct"],
            budget_var_abs=row["budget_var_abs"], budget_var_pct=row["budget_var_pct"],
        ),
        trend=row["trend"], is_anomaly=bool(row["is_anomaly"]),
        variance_bridge=_load_bridge(conn, row["metric"], period),
    )


def _load_computed_fact(conn, metric: str, period: str) -> ComputedFact:
    metric_id = _active_metric_id(conn, metric)
    fact = _fact_by_metric_id(conn, metric_id, period) if metric_id is not None else None
    if fact is None:
        raise HTTPException(
            status_code=404,
            detail=f"No computed facts for metric='{metric}' period='{period}'. Run /compute first.",
        )
    return fact


@router.get("/reports/{report_id}/grounding")
def report_grounding(report_id: int, _: CurrentUser = Depends(require_read)) -> dict:
    """Narrative Drill-Down (v3.0): per-sentence attribution to the exact computed
    facts and context docs that ground it. Deterministic, scoped to the caller's
    universe, computed on read (no LLM)."""
    conn = get_connection()
    try:
        row = conn.execute(
            f"""SELECT gr.narrative, gr.sources, gr.period, gr.metric_id
                FROM generated_reports gr JOIN metrics m ON m.id = gr.metric_id
                JOIN datasets d ON d.id = m.dataset_id
                WHERE gr.id = ? AND {_scope_pred('d')}""",
            (report_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
        fact = _fact_by_metric_id(conn, row["metric_id"], row["period"])
        if fact is None:
            raise HTTPException(status_code=404, detail="No computed fact for this report")
        # Rebuild the cited context snippets (bodies needed for causal attribution).
        try:
            cited = json.loads(row["sources"]) if row["sources"] else []
        except (ValueError, TypeError):
            cited = []
        ctx = []
        for s in cited:
            sid = str(s.get("id", ""))
            digits = sid.replace("ctx_", "")
            body = ""
            if digits.isdigit():
                b = conn.execute(
                    "SELECT body FROM context_documents WHERE id = ?", (int(digits),)
                ).fetchone()
                body = b["body"] if b else ""
            ctx.append(ContextSnippet(id=sid, type=s.get("type", "note"),
                                      title=s.get("title", ""), body=body))
        return attribute(row["narrative"], fact, ctx)
    finally:
        conn.close()
