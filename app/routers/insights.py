"""Advanced analytics (v5.0/5.3): deterministic forecasting, what-if scenarios,
cross-dataset correlations, and anomaly root-cause — with optional LLM narratives
that only phrase the computed numbers."""

from fastapi import APIRouter, Depends, HTTPException

from app.api import CurrentUser, require_member, require_read
from app.compute.cross_domain import cross_domain_correlations
from app.compute.forecast import backtest_mape, forecast, next_periods
from app.compute.root_cause import decompose as root_cause_decompose
from app.compute.scenario import run_scenario
from app.config import settings
from app.datasets import active_dataset_id, list_datasets
from app.db import get_connection
from app.generation.llm_client import LLMGenerationError, get_llm_client
from app.generation.prompts import (
    FORECAST_SYSTEM_PROMPT,
    ROOT_CAUSE_SYSTEM_PROMPT,
    build_forecast_prompt,
    build_root_cause_prompt,
)
from app.services import enforce_budget, log_llm_call

router = APIRouter(tags=["insights"])


def _metric_history(conn, ds: int, metric: str) -> list[dict]:
    rows = conn.execute(
        """SELECT cf.period, cf.value FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
           WHERE m.dataset_id = ? AND m.name = ? AND cf.value IS NOT NULL ORDER BY cf.period""",
        (ds, metric),
    ).fetchall()
    return [{"period": r["period"], "value": r["value"]} for r in rows]


def _forecast_inputs(metric: str, horizon: int) -> dict:
    """Shared by /forecast and /forecast/narrative: load history for the active
    dataset's metric and build the deterministic projection + backtest MAPE."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=400, detail="No active dataset")
        hist = _metric_history(conn, ds, metric)
        if len(hist) < 2:
            raise HTTPException(status_code=400, detail="Not enough history to forecast")
        unit = (conn.execute("SELECT unit FROM metrics WHERE dataset_id = ? AND name = ?",
                             (ds, metric)).fetchone() or {"unit": "USD"})["unit"]
    finally:
        conn.close()
    values = [h["value"] for h in hist]
    proj = forecast(values, horizon)
    periods = next_periods(hist[-1]["period"], horizon)
    return {
        "metric": metric, "unit": unit, "history": hist,
        "projections": [{"period": p, "value": v} for p, v in zip(periods, proj)],
        "mape": backtest_mape(values),
        # Sample size behind the backtest — a 0% MAPE on a handful of points is
        # not real accuracy, so the UI caveats the error when this is small.
        "n_history": len(values),
    }


@router.get("/forecast")
def get_forecast(metric: str, horizon: int = 3, _: CurrentUser = Depends(require_read)) -> dict:
    """Deterministic forward projection for a metric (v5.0). Holt-Winters/linear,
    with a backtest MAPE. The LLM is not involved."""
    metric = (metric or "").strip()
    if not metric:
        raise HTTPException(status_code=422, detail="metric is required")
    fc = _forecast_inputs(metric, max(1, min(12, horizon)))
    return {"metric": fc["metric"], "unit": fc["unit"], "history": fc["history"][-6:],
            "projections": fc["projections"], "mape": fc["mape"], "n_history": fc["n_history"]}


@router.get("/insights/cross-domain")
def cross_domain_endpoint(_: CurrentUser = Depends(require_read)) -> list[dict]:
    """Strong correlations between metrics in different datasets of the workspace
    (e.g. Marketing spend → FP&A revenue), with the best lead/lag. Deterministic."""
    conn = get_connection()
    try:
        ds_ids = [d["id"] for d in list_datasets(conn)]
        if len(ds_ids) < 2:
            return []
        return cross_domain_correlations(conn, ds_ids)
    finally:
        conn.close()


@router.post("/scenario")
def run_scenario_endpoint(payload: dict, _: CurrentUser = Depends(require_read)) -> dict:
    """What-if on a metric: apply price/volume/mix levers, get the projected value
    and impact instantly (deterministic, no LLM)."""
    metric = (payload.get("metric") or "").strip()
    if not metric:
        raise HTTPException(status_code=422, detail="metric is required")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=400, detail="No active dataset")
        period = (payload.get("period") or "").strip()
        row = conn.execute(
            """SELECT cf.value, cf.budget_var_abs FROM computed_facts cf
               JOIN metrics m ON m.id = cf.metric_id
               WHERE m.dataset_id = ? AND m.name = ?
               """ + ("AND cf.period = ? " if period else "")
            + "ORDER BY cf.period DESC LIMIT 1",
            ((ds, metric, period) if period else (ds, metric)),
        ).fetchone()
        if row is None or row["value"] is None:
            raise HTTPException(status_code=404, detail=f"No data for metric '{metric}'")
        base = float(row["value"])
        budget = (base - float(row["budget_var_abs"])) if row["budget_var_abs"] is not None else None
    finally:
        conn.close()

    def _num(k):
        try:
            return float(payload.get(k, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    return run_scenario(base, budget, price_pct=_num("price_pct"),
                        volume_pct=_num("volume_pct"), mix_pct=_num("mix_pct"))


@router.post("/forecast/narrative")
def forecast_narrative(payload: dict, user: CurrentUser = Depends(require_member)) -> dict:
    """Forward-looking commentary phrased by the LLM around the deterministic
    forecast numbers."""
    enforce_budget()
    metric = (payload.get("metric") or "").strip()
    if not metric:
        raise HTTPException(status_code=422, detail="metric is required")
    horizon = max(1, min(12, int(payload.get("horizon", 3))))
    fc = _forecast_inputs(metric, horizon)
    try:
        result, usage = get_llm_client().generate_narrative(
            FORECAST_SYSTEM_PROMPT,
            build_forecast_prompt(metric, fc["unit"], fc["history"], fc["projections"], fc["mape"]))
    except LLMGenerationError as exc:
        raise HTTPException(status_code=503, detail=f"Forecast narrative unavailable: {exc}") from exc
    log_llm_call("/forecast/narrative",
                 settings.openai_model if settings.llm_provider == "openai" else settings.anthropic_model,
                 usage.prompt_tokens, usage.completion_tokens, usage.cost_usd, None, user.id)
    return {"metric": metric, "projections": fc["projections"], "mape": fc["mape"],
            "narrative": result.narrative}


@router.get("/insights/root-cause")
def root_cause(metric: str, period: str, _: CurrentUser = Depends(require_read)) -> dict:
    """Deterministic root-cause decomposition of a metric's move in a period:
    z-score vs its own baseline, price/volume/mix attribution, correlated movers,
    and trend streak. No LLM — every number is computed."""
    metric = (metric or "").strip()
    period = (period or "").strip()
    if not metric or not period:
        raise HTTPException(status_code=422, detail="metric and period are required")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=400, detail="No active dataset")
        rc = root_cause_decompose(conn, ds, metric, period)
    finally:
        conn.close()
    if rc is None:
        raise HTTPException(status_code=404, detail=f"No data for '{metric}' in {period}")
    return rc


@router.post("/insights/root-cause/narrative")
def root_cause_narrative(payload: dict, user: CurrentUser = Depends(require_member)) -> dict:
    """Grounded 'why did it move' commentary phrased by the LLM around the
    deterministic root-cause decomposition."""
    enforce_budget()
    metric = (payload.get("metric") or "").strip()
    period = (payload.get("period") or "").strip()
    if not metric or not period:
        raise HTTPException(status_code=422, detail="metric and period are required")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=400, detail="No active dataset")
        rc = root_cause_decompose(conn, ds, metric, period)
    finally:
        conn.close()
    if rc is None:
        raise HTTPException(status_code=404, detail=f"No data for '{metric}' in {period}")
    try:
        result, usage = get_llm_client().generate_narrative(
            ROOT_CAUSE_SYSTEM_PROMPT, build_root_cause_prompt(rc))
    except LLMGenerationError as exc:
        raise HTTPException(status_code=503, detail=f"Root-cause narrative unavailable: {exc}") from exc
    log_llm_call("/insights/root-cause/narrative",
                 settings.openai_model if settings.llm_provider == "openai" else settings.anthropic_model,
                 usage.prompt_tokens, usage.completion_tokens, usage.cost_usd, None, user.id)
    return {**rc, "narrative": result.narrative}
