"""HTTP routes moved from app.main: ingest."""

import json
import re

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from app import audit
from app.api import require_read, require_write  # noqa: E402
from app.auth import CurrentUser
from app.compute import derived as derived_metrics
from app.compute.aggregate import (
    infer_aggregation_type,
)
from app.compute.formula import FormulaError
from app.compute.kpis import compute_and_store
from app.currency import Currency
from app.datasets import (
    _scope_pred,
    active_dataset_id,
    create_dataset,
    dataset_metadata,
)
from app.db import get_connection
from app.deps import shared_cache
from app.import_receipts import record_receipt
from app.ingestion.ingest import IngestValidationError, ingest_dataframe, parse_csv
from app.ingestion.mapping import MappingError, MappingSpec, match_budget_to_dataset, normalize, store_normalized
from app.ingestion.profiler import mapping_preview, profile_columns, suggest_mapping
from app.ingestion.templates import get_template as load_template
from app.ingestion.templates import save_template as save_mapping_template
from app.ingestion.upload import (
    MAX_UPLOAD_BYTES,
    UploadError,
    create_upload,
    get_upload,
    load_upload_frame,
    upload_sheet_period,
)
from app.kpis.library import suggest_import_defaults
from app.routes_common import _notify_anomalies
from app.schemas import (
    IngestSummary,
    KPIConfigPayload,
)

router = APIRouter()


@router.post("/ingest", response_model=IngestSummary)
def ingest(file: UploadFile, user: CurrentUser = Depends(require_write), currency: Currency = Form("USD")) -> IngestSummary:
    """Shortcut ingest for files already in the canonical format
    (period, metric, value, budget). Arbitrary layouts go through the
    flexible flow: /ingest/upload -> schema -> mapping.

    Sync def (not async): the parse/compute below is blocking CPU/IO work, so
    FastAPI runs it in a threadpool instead of stalling the event loop."""
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )
    try:
        df = parse_csv(raw)
    except IngestValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conn = get_connection()
    try:
        dataset_id = create_dataset(conn, name=file.filename or "CSV import",
                                    uploaded_by=user.id, uploaded_by_email=user.email, currency=currency)
        summary = ingest_dataframe(conn, df, dataset_id)
        compute_and_store(conn, dataset_id)
        _notify_anomalies(conn, dataset_id)
    finally:
        conn.close()
    shared_cache().bump_namespace()  # new data invalidates cached narratives
    return summary


@router.post("/ingest/upload")
def ingest_upload(file: UploadFile, _: CurrentUser = Depends(require_write), currency: Currency = Form("USD")) -> dict:
    # Sync def: create_upload does blocking pandas work (size cap enforced there).
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    conn = get_connection()
    try:
        return create_upload(conn, raw, file.filename or "upload.csv", currency=currency)
    except UploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/ingest/{upload_id}/schema")
def ingest_schema(upload_id: str, sheet: str | None = None, _: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        df = load_upload_frame(conn, upload_id, sheet)
        profile = profile_columns(df)
        if sheet:
            conn.execute("UPDATE uploads SET chosen_sheet = ? WHERE id = ?", (sheet, upload_id))
        conn.execute(
            "UPDATE uploads SET profiled_schema = ? WHERE id = ?",
            (json.dumps(profile), upload_id),
        )
        conn.commit()
        # A best-effort MappingSpec the mapping screen can pre-fill and the
        # user corrects, rather than building one from an empty form (or a
        # low-confidence guess dead-ending in a hard validation error before
        # they see anything).
        suggestion = suggest_mapping(profile, upload_sheet_period(conn, upload_id, sheet))
        preview = mapping_preview(df, MappingSpec(**suggestion["mapping"]))
        suggestion["warnings"].extend(preview["mapping_warnings"])
        if suggestion["warnings"]:
            suggestion["confidence"] = "low"
        return {**profile, "suggested_mapping": suggestion["mapping"], "likely_total_rows": preview["likely_total_rows"],
                "mapping_warnings": suggestion["warnings"], "mapping_confidence": suggestion["confidence"]}
    except UploadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.post("/ingest/{upload_id}/mapping")
def ingest_mapping(upload_id: str, mapping: MappingSpec, save_template: bool = False,
                   target_dataset_id: int | None = None,
                   user: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        df = load_upload_frame(conn, upload_id)
        try:
            canonical = normalize(df, mapping)
        except MappingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        up = conn.execute(
            "SELECT filename, column_signature, currency FROM uploads WHERE id = ?", (upload_id,)
        ).fetchone()
        existing = conn.execute(
            f"SELECT id FROM datasets WHERE source_upload_id=? AND {_scope_pred()} ORDER BY id DESC LIMIT 1",
            (upload_id,)).fetchone()
        if target_dataset_id is not None:
            existing = conn.execute(f"SELECT id FROM datasets WHERE id=? AND {_scope_pred()}",
                                    (target_dataset_id,)).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
        dataset_id = existing["id"] if existing else create_dataset(
            conn, name=up["filename"] if up else upload_id, source_upload_id=upload_id,
            uploaded_by=user.id, uploaded_by_email=user.email, currency=up["currency"] if up else "USD")
        summary = store_normalized(conn, canonical, dataset_id, replace=bool(existing),
                                   actor_id=user.id, actor_email=user.email)
        conn.execute(
            "UPDATE uploads SET mapping = ? WHERE id = ?",
            (mapping.model_dump_json(), upload_id),
        )
        conn.commit()

        template_id = None
        if save_template:
            template_id = save_mapping_template(conn, up["column_signature"], mapping.model_dump())

        derived_metrics.refresh(conn, dataset_id, actor_id=user.id, actor_email=user.email,
                                report=summary["import_report"])
        facts = compute_and_store(conn, dataset_id)
        audit.record(conn, "import", "dataset", dataset_id, actor_id=user.id, actor_email=user.email,
                     summary={"name": up["filename"] if up else upload_id,
                              "rows": int(summary["import_report"].get("rows_kept", 0))})
        record_receipt(conn, dataset_id, kind="import", filename=up["filename"] if up else upload_id,
                       mapping=mapping.model_dump(), report=summary["import_report"],
                       written_pairs=canonical.attrs["written_pairs"])
        _notify_anomalies(conn, dataset_id)
        shared_cache().bump_namespace()
        return {**summary, "dataset_id": dataset_id, **dataset_metadata(conn, dataset_id),
                "facts_computed": int(len(facts)), "template_id": template_id}
    except UploadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.post("/ingest/{upload_id}/join-budget")
def ingest_join_budget(upload_id: str, payload: dict,
                       user: CurrentUser = Depends(require_write)) -> dict:
    """Attach a budget-only upload to an EXISTING dataset's metrics, instead of
    creating a new dataset. A budget file is a correction/addition to data
    that already exists — its numbers fill in metric_values.budget on rows
    that already have an actual, they never create a new metric.

    Body: {"target_dataset_id": int, "mapping": <MappingSpec>,
           "metric_suffix": str | null}. metric_suffix handles the funnel
    shape where actuals are "<label> <measure>" (e.g. "Paid Search spend_usd")
    but the budget file's metric column is just the label ("Paid Search") —
    it's appended before matching. Returns a report (matched / near-miss /
    unmatched) rather than a bare success, since a silent partial join would
    be worse than the hard error this replaces."""
    field = payload.get("field", "budget")
    if field not in ("budget", "forecast"):
        raise HTTPException(status_code=422, detail="field must be budget or forecast")
    target_dataset_id = payload.get("target_dataset_id")
    if not target_dataset_id:
        raise HTTPException(status_code=422, detail="target_dataset_id is required")
    mapping_payload = payload.get("mapping") or {}
    try:
        mapping = MappingSpec(**mapping_payload)
    except Exception as exc:  # noqa: BLE001 - surface a 422 with the pydantic detail, not a 500
        raise HTTPException(status_code=422, detail=f"Invalid mapping: {exc}") from exc

    conn = get_connection()
    try:
        target = conn.execute(
            f"SELECT id FROM datasets WHERE id = ? AND {_scope_pred()}", (target_dataset_id,)
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail=f"Dataset {target_dataset_id} not found")

        df = load_upload_frame(conn, upload_id)
        try:
            canonical = normalize(df, mapping)
        except MappingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        suffix = (payload.get("metric_suffix") or "").strip()
        if suffix:
            canonical["metric"] = canonical["metric"].astype(str).str.strip() + " " + suffix

        report = match_budget_to_dataset(conn, canonical, target_dataset_id,
                                         field=field, actor_id=user.id, actor_email=user.email)
        derived_metrics.refresh(conn, target_dataset_id, actor_id=user.id, actor_email=user.email, report=report)
        up = get_upload(conn, upload_id)
        record_receipt(conn, target_dataset_id, kind=f"{field}_join", filename=up["filename"],
                       mapping={**mapping.model_dump(), "metric_suffix": suffix}, report=report,
                       written_pairs=canonical.attrs["written_pairs"])
        shared_cache().bump_namespace()
        return {**report, "import_report": report, "dataset_id": target_dataset_id}
    except UploadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


_GROUP_DIM = re.compile(r"account_?(group|type|class|section)|^(group|class|section|category)$", re.I)


def _metric_ledger_hints(canonical) -> dict[str, tuple[str | None, str | None]]:
    """metric -> (account group, account code) from the upload itself: the
    most common value of an account-group-like dimension, and the first id.
    These are the customer's own classification, so the KPI picker trusts
    them over name keywords."""
    hints: dict[str, tuple[str | None, str | None]] = {}
    for metric, rows in canonical.groupby("metric", sort=False):
        group = None
        groups: list[str] = []
        for raw in rows["dimensions"].dropna():
            try:
                dims = json.loads(raw)
            except (TypeError, ValueError):
                continue
            groups += [str(v) for k, v in dims.items() if _GROUP_DIM.search(k) and v not in ("", "nan", "None")]
        if groups:
            group = max(set(groups), key=groups.count)
        ids = rows["id"].dropna() if "id" in rows else []
        code = str(ids.iloc[0]) if len(ids) else None
        hints[metric] = (group, code)
    return hints


@router.get("/ingest/{upload_id}/metrics")
def ingest_metrics(upload_id: str, _: CurrentUser = Depends(require_write)) -> list[dict]:
    """Distinct metric names in this upload's normalized data, each with a
    KPI-library suggestion for auto-filling category/unit/direction."""
    conn = get_connection()
    try:
        try:
            row = get_upload(conn, upload_id)
        except UploadError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not row["mapping"]:
            raise HTTPException(status_code=409, detail="Confirm a mapping first")
        mapping = MappingSpec(**json.loads(row["mapping"]))
        df = load_upload_frame(conn, upload_id)
        canonical = normalize(df, mapping)
        hints = _metric_ledger_hints(canonical)
        return [
            {"source_metric": m,
             "suggestion": suggest_import_defaults(m, *hints.get(m, (None, None)))}
            for m in sorted(canonical["metric"].unique())
        ]
    finally:
        conn.close()


@router.get("/templates/{template_id}")
def get_mapping_template(template_id: int, _: CurrentUser = Depends(require_write)) -> dict:
    conn = get_connection()
    try:
        tpl = load_template(conn, template_id)
    finally:
        conn.close()
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
    return tpl


@router.post("/kpis")
def configure_kpis(payload: KPIConfigPayload, _: CurrentUser = Depends(require_write)) -> dict:
    """Persist KPI selections and apply them to the metrics table so compute,
    generation, and the UI all see display name, category, unit, and
    direction_good (down = lower is better, e.g. OpEx)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=409, detail="No active dataset — import data first")
        for kpi in payload.kpis:
            agg = getattr(kpi, "aggregation_type", None) or infer_aggregation_type(
                kpi.display_name or kpi.source_metric, kpi.unit
            )
            conn.execute(
                """
                INSERT INTO kpi_configs (dataset_id, source_metric, display_name, category, unit, direction_good, budget_source, aggregation_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_id, source_metric) DO UPDATE SET
                    display_name=excluded.display_name, category=excluded.category,
                    unit=excluded.unit, direction_good=excluded.direction_good,
                    budget_source=excluded.budget_source, aggregation_type=excluded.aggregation_type
                """,
                (ds, kpi.source_metric, kpi.display_name, kpi.category, kpi.unit,
                 kpi.direction_good, kpi.budget_source, agg),
            )
            conn.execute(
                "UPDATE metrics SET category = ?, unit = ?, direction_good = ? WHERE name = ? AND dataset_id = ?",
                (kpi.category, kpi.unit, kpi.direction_good, kpi.source_metric, ds),
            )
        conn.commit()
    finally:
        conn.close()
    shared_cache().bump_namespace()
    return {"kpis_configured": len(payload.kpis)}


@router.post("/kpis/derived", status_code=201)
def create_derived_kpi(payload: dict, user: CurrentUser = Depends(require_write)) -> dict:
    """v5.0 Custom KPI Builder: define a derived metric by formula, e.g.
    {"name": "Gross Margin %", "formula": "([Net Revenue]-[COGS])/[Net Revenue]*100"}.
    The value is computed deterministically per period, so it gets full delta/
    trend/anomaly + narrative support and passes the faithfulness guard."""
    name = (payload.get("name") or "").strip()
    formula = (payload.get("formula") or "").strip()
    if not name or not formula:
        raise HTTPException(status_code=422, detail="name and formula are required")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=409, detail="No active dataset — import data first")
        try:
            did = derived_metrics.create_derived_metric(
                conn, ds, name, formula,
                unit=payload.get("unit", "USD"), category=payload.get("category", "Derived"),
                direction_good=payload.get("direction_good", "up"))
        except FormulaError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        report = {}
        derived_metrics.materialize(conn, ds, actor_id=user.id, actor_email=user.email, report=report)
        compute_and_store(conn, ds)
        audit.record(conn, "create", "derived_kpi", did, actor_id=user.id,
                     actor_email=user.email, summary={"name": name, "formula": formula})
    finally:
        conn.close()
    shared_cache().bump_namespace()
    return {"id": did, "name": name, "import_report": report}


@router.get("/kpis")
def list_kpis(_: CurrentUser = Depends(require_read)) -> dict:
    """Current KPI selections for the active dataset (drives the board), plus
    the dataset's metrics that are NOT selected (so the UI can add them back)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return {"selected": [], "available": [], **dataset_metadata(conn, ds)}
        selected = [dict(r) for r in conn.execute(
            """SELECT id, source_metric, display_name, category, unit, direction_good, aggregation_type
               FROM kpi_configs WHERE dataset_id = ? ORDER BY source_metric""",
            (ds,),
        ).fetchall()]
        metadata = dataset_metadata(conn, ds)
        for s in selected:
            s.update(metadata)
            s["aggregation_type"] = s.get("aggregation_type") or infer_aggregation_type(
                s["display_name"], s["unit"]
            )
        chosen = {s["source_metric"] for s in selected}
        available = [r["name"] for r in conn.execute(
            "SELECT name FROM metrics WHERE dataset_id = ? ORDER BY name", (ds,)
        ).fetchall() if r["name"] not in chosen]
        return {"selected": selected, "available": available, **metadata}
    finally:
        conn.close()


@router.delete("/kpis/{kpi_id}", status_code=204)
def delete_kpi(kpi_id: int, _: CurrentUser = Depends(require_write)) -> None:
    """Remove one KPI from the board (its data stays; it's just deselected)."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        conn.execute("DELETE FROM kpi_configs WHERE id = ? AND dataset_id = ?", (kpi_id, ds))
        conn.commit()
    finally:
        conn.close()
    shared_cache().bump_namespace()
