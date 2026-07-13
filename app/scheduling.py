"""Scheduled jobs (v3.1): the backbone for Phase 2 (scheduled anomaly scans) and
Phase 4 (scheduled digests). Render's free tier has no built-in cron, so an
external pinger (cron-job.org / GitHub Actions) calls POST /internal/scheduler/tick
with the shared SCHEDULER_TOKEN; that endpoint calls run_due_jobs() here.

A job is (kind, cadence, dataset). run_due_jobs runs every enabled job whose
next_run_at has passed, then advances it. Date math is done in Python and stored
as ISO-8601 text so the same code works on SQLite (dev) and Postgres (prod).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

KINDS = ("digest", "anomaly_scan", "connector_sync")
CADENCES = ("daily", "weekly", "monthly")
# After this many consecutive failures, alert the operator via configured channels.
FAIL_ALERT_THRESHOLD = 3


# --------------------------------------------------------------------------
# Cadence math
# --------------------------------------------------------------------------
def advance(now: datetime, cadence: str) -> datetime:
    """The next run time after `now` for a cadence. Monthly keeps the day-of-month
    where possible (clamped for short months)."""
    if cadence == "daily":
        return now + timedelta(days=1)
    if cadence == "weekly":
        return now + timedelta(days=7)
    if cadence == "monthly":
        month = now.month + 1
        year = now.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        # Clamp the day so e.g. Jan 31 -> Feb 28.
        for day in (now.day, 30, 29, 28):
            try:
                return now.replace(year=year, month=month, day=day)
            except ValueError:
                continue
    raise ValueError(f"Unknown cadence: {cadence!r}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Job CRUD
# --------------------------------------------------------------------------
def list_jobs(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT id, kind, cadence, dataset_id, config, enabled, last_run_at,
                  next_run_at, last_status, last_error, fail_count, created_at
           FROM scheduled_jobs ORDER BY id"""
    ).fetchall()
    return [
        {
            "id": r["id"], "kind": r["kind"], "cadence": r["cadence"],
            "dataset_id": r["dataset_id"],
            "config": json.loads(r["config"]) if r["config"] else {},
            "enabled": bool(r["enabled"]),
            "last_run_at": str(r["last_run_at"]) if r["last_run_at"] else None,
            "next_run_at": str(r["next_run_at"]) if r["next_run_at"] else None,
            "last_status": r["last_status"], "last_error": r["last_error"],
            "fail_count": r["fail_count"] or 0,
            "created_at": str(r["created_at"]) if r["created_at"] else None,
        }
        for r in rows
    ]


def create_job(conn, kind: str, cadence: str, *, dataset_id: int | None = None,
               config: dict | None = None, enabled: bool = True) -> int:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if cadence not in CADENCES:
        raise ValueError(f"cadence must be one of {CADENCES}")
    # next_run_at = now so the first tick fires it, then it advances by cadence.
    cur = conn.execute(
        """INSERT INTO scheduled_jobs (kind, cadence, dataset_id, config, enabled, next_run_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (kind, cadence, dataset_id, json.dumps(config or {}), bool(enabled), _now().isoformat()),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_job_enabled(conn, job_id: int, enabled: bool) -> None:
    conn.execute("UPDATE scheduled_jobs SET enabled = ? WHERE id = ?", (bool(enabled), job_id))
    conn.commit()


def delete_job(conn, job_id: int) -> bool:
    exists = conn.execute("SELECT 1 FROM scheduled_jobs WHERE id = ?", (job_id,)).fetchone()
    if not exists:
        return False
    conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
    conn.commit()
    return True


# --------------------------------------------------------------------------
# Digest history (Phase 4: history + period-over-period comparison)
# --------------------------------------------------------------------------
def record_digest_run(conn, dataset_id: int | None, period: str, top_n: int,
                      items: list[dict], cost_usd: float | None, trigger: str) -> int:
    cur = conn.execute(
        """INSERT INTO digest_runs (dataset_id, period, top_n, items, cost_usd, trigger)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (dataset_id, period, top_n, json.dumps(items), cost_usd, trigger),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_digest_runs(conn, dataset_id: int | None = None, limit: int = 24) -> list[dict]:
    if dataset_id is not None:
        rows = conn.execute(
            """SELECT id, dataset_id, period, top_n, items, cost_usd, trigger, created_at
               FROM digest_runs WHERE dataset_id = ? ORDER BY id DESC LIMIT ?""",
            (dataset_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, dataset_id, period, top_n, items, cost_usd, trigger, created_at
               FROM digest_runs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"], "dataset_id": r["dataset_id"], "period": r["period"],
            "top_n": r["top_n"],
            "items": json.loads(r["items"]) if r["items"] else [],
            "cost_usd": r["cost_usd"], "trigger": r["trigger"],
            "created_at": str(r["created_at"]) if r["created_at"] else None,
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# The tick: run every due job
# --------------------------------------------------------------------------
def _latest_period(conn, dataset_id: int) -> str | None:
    row = conn.execute(
        """SELECT MAX(cf.period) AS p FROM computed_facts cf
           JOIN metrics m ON m.id = cf.metric_id WHERE m.dataset_id = ?""",
        (dataset_id,),
    ).fetchone()
    return row["p"] if row and row["p"] else None


def _resolve_dataset(conn, job: dict) -> int | None:
    from app.datasets import active_dataset_id
    return job["dataset_id"] if job["dataset_id"] is not None else active_dataset_id(conn)


def _run_digest_job(conn, job: dict, llm_client) -> dict:
    from app.digest.digest import generate_digest
    from app.notifications.scheduler import deliver

    if llm_client is None:
        return {"skipped": "no LLM configured"}
    ds = _resolve_dataset(conn, job)
    if ds is None:
        return {"skipped": "no dataset"}
    period = _latest_period(conn, ds)
    if not period:
        return {"skipped": "no data"}
    top_n = int(job["config"].get("top_n", 5))
    out = generate_digest(conn, period, llm_client, top_n=top_n, dataset_id=ds)
    items = [it.model_dump() for it in out.items]
    record_digest_run(conn, ds, period, top_n, items, out.cost_usd, "scheduled")
    delivery = deliver(conn, "digest_generated", period=period, items=[
        {"metric": it.metric, "period": period, "value": it.headline,
         "delta": f"{it.budget_var_pct:+.1f}% vs plan" if it.budget_var_pct is not None else "",
         "narrative": it.detail}
        for it in out.items
    ])
    return {"period": period, "items": len(items), "delivered_to": delivery["sent"]}


def _run_anomaly_job(conn, job: dict, llm_client) -> dict:
    """Recompute the dataset, then alert on anomalies in the latest period. If an
    LLM is available each anomaly gets a one-line grounded narrative; otherwise
    the deterministic value/delta is sent (Phase 2)."""
    from app.compute.kpis import compute_and_store
    from app.notifications.scheduler import deliver

    ds = _resolve_dataset(conn, job)
    if ds is None:
        return {"skipped": "no dataset"}
    compute_and_store(conn, ds)
    period = _latest_period(conn, ds)
    if not period:
        return {"skipped": "no data"}
    rows = conn.execute(
        """SELECT m.name AS metric, cf.value, cf.mom_pct, m.unit
           FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
           WHERE m.dataset_id = ? AND cf.period = ? AND cf.is_anomaly = 1""",
        (ds, period),
    ).fetchall()
    if not rows:
        return {"period": period, "anomalies": 0}
    items = [{
        "metric": r["metric"], "period": period,
        "value": f"{r['value']:,.0f}",
        "delta": f"{r['mom_pct']:+.1f}% MoM" if r["mom_pct"] is not None else "",
        "narrative": _anomaly_narrative(conn, ds, r["metric"], period, llm_client),
    } for r in rows]
    delivery = deliver(conn, "anomaly_detected", period=period, items=items)
    return {"period": period, "anomalies": len(items), "delivered_to": delivery["sent"]}


def _anomaly_narrative(conn, ds: int, metric: str, period: str, llm_client) -> str:
    """Best-effort one-line narrative for an anomalous metric. Never raises — a
    generation failure just yields an empty string and the alert still sends."""
    if llm_client is None:
        return ""
    try:
        from app.digest.digest import _load_facts_for_period
        facts = {f.metric: f for f in _load_facts_for_period(conn, period, dataset_id=ds)}
        fact = facts.get(metric)
        if fact is None:
            return ""
        from app.generation.generate import generate_insight
        out = generate_insight(fact, [], llm_client)
        return out.narrative or ""
    except Exception:  # noqa: BLE001 - narrative is a nice-to-have, alert must still fire
        return ""


def _status_of(result: dict) -> tuple[str, str | None]:
    if "error" in result:
        return "error", str(result["error"])
    if "skipped" in result:
        return "skipped", str(result["skipped"])
    return "ok", None


def _alert_job_failure(conn, job: dict, error: str) -> None:
    """After repeated failures, tell the operator via the configured channels."""
    from app.notifications.scheduler import deliver
    try:
        deliver(conn, "anomaly_detected", items=[{
            "metric": f"Scheduled {job['kind']} job #{job['id']}",
            "period": "", "value": "failing",
            "delta": f"{FAIL_ALERT_THRESHOLD}+ consecutive failures",
            "narrative": f"The scheduled {job['kind']} job keeps failing: {error[:200]}",
        }])
    except Exception:  # noqa: BLE001 - alerting failure must not abort the tick
        pass


def _run_connector_sync_job(conn, job: dict) -> dict:
    """Sync every enabled connector (each in its own workspace scope). Replaces
    manual CSV upload with scheduled pulls (Phase: Live Data Connectors)."""
    from app import connectors
    synced, failed = 0, 0
    for cid, ws in connectors.all_enabled(conn):
        r = connectors.sync_connector(conn, cid, ws)
        if r.get("status") == "ok":
            synced += 1
        else:
            failed += 1
    return {"connectors_synced": synced, "connectors_failed": failed}


def run_due_jobs(conn, now: datetime | None = None, llm_client=None) -> dict:
    """Run every enabled job whose next_run_at has passed. Operates strictly on
    the real (non-demo) universe. Logs each run, tracks last status + consecutive
    failures, and alerts the operator after repeated failures."""
    from app.datasets import set_demo_scope

    set_demo_scope(False)
    now = now or _now()
    ran = []
    for job in list_jobs(conn):
        if not job["enabled"]:
            continue
        nxt = _parse(job["next_run_at"])
        if nxt is not None and nxt > now:
            continue
        t0 = time.perf_counter()
        try:
            if job["kind"] == "digest":
                result = _run_digest_job(conn, job, llm_client)
            elif job["kind"] == "anomaly_scan":
                result = _run_anomaly_job(conn, job, llm_client)
            elif job["kind"] == "connector_sync":
                result = _run_connector_sync_job(conn, job)
            else:
                result = {"skipped": f"unknown kind {job['kind']}"}
        except Exception as e:  # noqa: BLE001 - one job's failure must not abort the tick
            result = {"error": str(e)}
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        status, detail = _status_of(result)
        fail_count = (job["fail_count"] + 1) if status == "error" else 0

        conn.execute(
            """INSERT INTO scheduler_runs (job_id, kind, status, detail, latency_ms)
               VALUES (?, ?, ?, ?, ?)""",
            (job["id"], job["kind"], status, detail, latency_ms),
        )
        conn.execute(
            """UPDATE scheduled_jobs
               SET last_run_at = ?, next_run_at = ?, last_status = ?, last_error = ?,
                   fail_count = ? WHERE id = ?""",
            (now.isoformat(), advance(now, job["cadence"]).isoformat(),
             status, detail if status == "error" else None, fail_count, job["id"]),
        )
        conn.commit()
        if status == "error" and fail_count == FAIL_ALERT_THRESHOLD:
            _alert_job_failure(conn, job, detail or "")
        ran.append({"id": job["id"], "kind": job["kind"], "status": status,
                    "latency_ms": latency_ms, **result})
    return {"now": now.isoformat(), "ran": ran}
