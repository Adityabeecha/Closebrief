"""Shared support for HTTP routes and application startup."""

import time
from collections import defaultdict, deque

from app.context.store import ContextStore
from app.datasets import (
    active_dataset_id,
)
from app.db import get_connection
from app.deps import shared_embedder, shared_vector_store
from app.notifications.scheduler import deliver as notif_deliver
from app.services import bg_executor as _bg_executor  # noqa: E402


def _notify_async(event: str, period: str | None = None, items: list[dict] | None = None,
                  detected_at: float | None = None) -> None:
    """Fan a notification event out to configured channels on the background pool
    — delivery (SMTP/HTTP) must never add latency or failures to the request.
    When `detected_at` is given, record the detection→delivery latency."""
    from app.datasets import current_workspace

    workspace_id = current_workspace()

    def _run():
        try:
            conn = get_connection()
            try:
                notif_deliver(conn, event, workspace_id=workspace_id, period=period, items=items)
                if detected_at is not None:
                    _ALERT_LATENCIES.append((time.time() - detected_at) * 1000)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - best-effort by design
            pass

    _bg_executor.submit(_run)


def _notify_anomalies(conn, dataset_id: int) -> None:
    """After a compute pass: alert channels about anomalies in the dataset's
    latest period (if any). Stamps detection time so alert latency is tracked."""
    detected_at = time.time()
    rows = conn.execute(
        """
        SELECT m.name AS metric, cf.period, cf.value, cf.mom_pct
        FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
        WHERE m.dataset_id = ? AND cf.is_anomaly = 1 AND cf.period = (
            SELECT MAX(cf2.period) FROM computed_facts cf2
            JOIN metrics m2 ON m2.id = cf2.metric_id WHERE m2.dataset_id = ?
        )
        """,
        (dataset_id, dataset_id),
    ).fetchall()
    if not rows:
        return
    items = [{
        "metric": r["metric"], "period": r["period"],
        "value": f"{r['value']:,.0f}",
        "delta": f"{r['mom_pct']:+.1f}% MoM" if r["mom_pct"] is not None else "",
    } for r in rows]
    _notify_async("anomaly_detected", items=items, detected_at=detected_at)


_ALERT_LATENCIES: deque = deque(maxlen=200)


def _active_domain_slug(conn) -> str:
    ds = active_dataset_id(conn)
    if ds is not None:
        row = conn.execute("SELECT domain FROM datasets WHERE id = ?", (ds,)).fetchone()
        if row and row["domain"]:
            return row["domain"]
    return "fpa"


def _context_store(conn) -> ContextStore:
    return ContextStore(conn, shared_embedder(), shared_vector_store())


_LATENCIES: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
