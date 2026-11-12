"""HTTP routes moved from app.main: datasets."""

from fastapi import APIRouter, Depends, HTTPException

from app.api import require_read, require_write  # noqa: E402
from app.auth import CurrentUser
from app.compute.aggregate import (
    GRANULARITIES,
    period_key,
    sort_key,
)
from app.currency import validate_currency
from app.datasets import (
    _scope_pred,
    active_dataset_id,
    dataset_metadata,
    delete_dataset,
    list_datasets,
    set_active,
)
from app.db import get_connection
from app.deps import shared_cache
from app.domains import registry
from app.import_receipts import list_receipts

router = APIRouter()


@router.get("/datasets/{dataset_id}/receipts")
def dataset_receipts(dataset_id: int, _: CurrentUser = Depends(require_read)) -> list[dict]:
    conn = get_connection()
    try:
        if conn.execute(f"SELECT id FROM datasets WHERE id = ? AND {_scope_pred()}", (dataset_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
        return list_receipts(conn, dataset_id)
    finally:
        conn.close()


@router.get("/datasets")
def get_datasets(_: CurrentUser = Depends(require_read)) -> dict:
    """All datasets + which is active, for the dashboard's dataset switcher."""
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        return {"datasets": list_datasets(conn), "active_id": ds, **dataset_metadata(conn, ds)}
    finally:
        conn.close()


@router.post("/datasets/{dataset_id}/activate")
def activate_dataset(dataset_id: int, user: CurrentUser = Depends(require_read)) -> dict:
    # Switching dataset is navigation. The browser sends X-Dataset-Id on every
    # request, so the switch itself lives client-side. Writers also store it as
    # their default for the next visit; read-only sessions (demo, guests) only
    # validate, so one visitor can never change what other visitors see.
    conn = get_connection()
    try:
        exists = conn.execute(
            f"SELECT 1 FROM datasets WHERE id = ? AND {_scope_pred()}", (dataset_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
        metadata = dataset_metadata(conn, dataset_id)
        if user.role in ("analyst", "admin"):
            set_active(conn, dataset_id)
    finally:
        conn.close()
    return {"active_id": dataset_id, **metadata}


@router.patch("/datasets/{dataset_id}")
def rename_dataset(dataset_id: int, payload: dict, _: CurrentUser = Depends(require_write)) -> dict:
    """Rename a dataset and/or set its analytics domain or currency."""
    name = payload.get("name")
    domain = payload.get("domain")
    if "fiscal_year_start" in payload and (type(payload["fiscal_year_start"]) is not int or not 1 <= payload["fiscal_year_start"] <= 12):
        raise HTTPException(status_code=422, detail="fiscal_year_start must be an integer from 1 to 12")
    if "currency" in payload:
        try:
            validate_currency(payload["currency"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if domain is not None and not registry.has(domain):
        raise HTTPException(status_code=422, detail=f"Unknown domain: {domain}")
    conn = get_connection()
    try:
        if conn.execute(f"SELECT 1 FROM datasets WHERE id = ? AND {_scope_pred()}", (dataset_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
        if name:
            conn.execute("UPDATE datasets SET name = ? WHERE id = ?", (name.strip()[:120], dataset_id))
        if domain is not None:
            conn.execute("UPDATE datasets SET domain = ? WHERE id = ?", (domain, dataset_id))
        if "currency" in payload:
            conn.execute("UPDATE datasets SET currency = ? WHERE id = ?", (payload["currency"], dataset_id))
        if "fiscal_year_start" in payload:
            conn.execute("UPDATE datasets SET fiscal_year_start = ? WHERE id = ?", (payload["fiscal_year_start"], dataset_id))
        conn.commit()
        metadata = dataset_metadata(conn, dataset_id)
    finally:
        conn.close()
    shared_cache().bump_namespace()
    return {"id": dataset_id, "name": name, "domain": domain, **metadata}


@router.delete("/datasets/{dataset_id}", status_code=204)
def remove_dataset(dataset_id: int, _: CurrentUser = Depends(require_write)) -> None:
    conn = get_connection()
    try:
        ok = delete_dataset(conn, dataset_id)
    finally:
        conn.close()
    if not ok:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
    shared_cache().bump_namespace()


@router.get("/periods")
def list_periods(granularity: str = "month", _: CurrentUser = Depends(require_read)) -> list[str]:
    """Period keys the active dataset covers at the given granularity
    (month=YYYY-MM, quarter=YYYY-Qn, year=YYYY), oldest to newest."""
    if granularity not in GRANULARITIES:
        raise HTTPException(status_code=422, detail=f"granularity must be one of {GRANULARITIES}")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return []
        rows = conn.execute(
            """
            SELECT DISTINCT cf.period FROM computed_facts cf
            JOIN metrics m ON m.id = cf.metric_id
            WHERE m.dataset_id = ?
            """,
            (ds,),
        ).fetchall()
        fiscal_start = dataset_metadata(conn, ds)["fiscal_year_start"]
        keys = {period_key(r["period"], granularity, fiscal_start) for r in rows}
        return sorted(keys, key=lambda key: sort_key(key, fiscal_start))
    finally:
        conn.close()
