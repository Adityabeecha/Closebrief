"""Read-only close summaries. A month is where commentary is drafted and
approved; a quarter or year close rolls up its months with the same rules, and
its checklist points at the first month that still needs each step."""

import calendar
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app import audit, scheduling
from app.api import CurrentUser, require_read, require_write
from app.board_pack import _period_title
from app.close_control import lock_status, serialize_writes
from app.close_export import XLSX_TYPE, build_close_workbook, content_disposition
from app.compute.aggregate import GRANULARITIES, months_in, period_key, sort_key
from app.compute.availability import has_usable_value, needs_reason
from app.context.store import _row_to_doc
from app.datasets import _scope_pred, active_dataset_id, current_workspace, dataset_metadata, timestamp_text
from app.db import get_connection
from app.retrieval.retrieve import _effective_ok, _metric_ok

router = APIRouter(prefix="/close", tags=["close"])


_PERIOD_RE = {"month": r"\d{4}-(0[1-9]|1[0-2])", "quarter": r"\d{4}-Q[1-4]", "year": r"\d{4}"}


def _granularity_of(period):
    """"month", "quarter" or "year" for a valid period key, else None."""
    if not period or period.startswith("0000"):
        return None
    return next((g for g, pat in _PERIOD_RE.items() if re.fullmatch(pat, period)), None)


def _label(period):
    if "-Q" in period:
        year, q = period.split("-Q")
        return f"Q{q} {year}"
    if "-" not in period:
        return period
    year, month = map(int, period.split("-"))
    return f"{calendar.month_name[month]} {year}"


_COUNTS = ("kpis", "drafted", "approved", "checked", "needs_reason", "exceptions", "flagged")


def _finish(period, g, months, shared_months, shared_at):
    """Score one close (a month, or a quarter/year from its months' counts)."""
    n = g["kpis"]
    share = len(shared_months) / len(months) if months else 0
    coverage = (1 - g["needs_reason"] / g["flagged"] if g["flagged"] else 1) if g["drafted"] else 0
    progress = 0.2 + 0.2 * g["checked"] / n + 0.2 * coverage + 0.3 * g["approved"] / n + 0.1 * share
    is_shared = bool(months) and len(shared_months) == len(months)
    return {
        "period": period,
        "label": _label(period),
        "status": "shared" if is_shared else "in_progress" if g["drafted"] else "not_started",
        **g,
        "progress": round(progress, 2),
        "shared_at": shared_at if is_shared else None,
        "months": sorted(months),
    }


def _summaries(conn, ds, granularity="month"):
    """Progress: 20% imported, 20% checked share, 20% reason coverage,
    30% approved share, and 10% shared. Reason coverage counts only once
    something is drafted (an untouched close shows just its import); empty
    reason sets are then fully covered. Checked means the latest report
    passed the existing faithfulness check.
    """
    if ds is None:
        return []
    meta = dataset_metadata(conn, ds)
    fiscal_start, threshold = meta["fiscal_year_start"], meta["reason_threshold_pct"]
    rows = conn.execute(
        """SELECT cf.period, cf.metric_id, cf.value, m.name, cf.is_anomaly,
        cf.budget_var_pct, gr.id AS report_id, gr.review_status, gr.faithfulness,
        COALESCE(k.unit, m.unit) AS unit, mv.source_rows
        FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
        LEFT JOIN kpi_configs k ON k.dataset_id = m.dataset_id AND k.source_metric = m.name
        LEFT JOIN metric_values mv ON mv.metric_id = m.id AND mv.period = cf.period
        LEFT JOIN generated_reports gr ON gr.id = (SELECT MAX(g.id) FROM generated_reports g
            WHERE g.metric_id = cf.metric_id AND g.period = cf.period
              AND (g.prompt_version IS NULL OR g.prompt_version NOT LIKE '%-digest'))
        WHERE m.dataset_id = ?
          AND (NOT EXISTS (SELECT 1 FROM kpi_configs k WHERE k.dataset_id=m.dataset_id)
               OR EXISTS (SELECT 1 FROM kpi_configs k WHERE k.dataset_id=m.dataset_id AND k.source_metric=m.name))
        ORDER BY cf.period DESC""",
        (ds,),
    ).fetchall()
    docs = [_row_to_doc(r) for r in conn.execute(f"SELECT * FROM context_documents WHERE {_scope_pred()}").fetchall()]
    runs = scheduling.list_digest_runs(conn, ds, limit=2147483647)
    shared = {}
    for run in runs:
        timestamp = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        shared.setdefault(run["period"], timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    groups = {}
    for row in rows:
        if not has_usable_value(row["metric_id"], row["value"], row["unit"], row["source_rows"]):
            continue
        period = row["period"]
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period) or period.startswith("0000"):
            continue
        group = groups.setdefault(
            period,
            {"kpis": 0, "drafted": 0, "approved": 0, "checked": 0, "needs_reason": 0, "exceptions": 0, "flagged": 0},
        )
        group["kpis"] += 1
        group.setdefault("ids", set()).add(row["metric_id"])
        group["drafted"] += int(row["report_id"] is not None)
        group["approved"] += int(row["review_status"] == "approved")
        group["checked"] += int(row["faithfulness"] == "passed")
        group["exceptions"] += int(bool(row["is_anomaly"]))
        flagged = needs_reason(row["is_anomaly"], row["budget_var_pct"], threshold)
        if flagged:
            group["flagged"] += 1
            if not any(_metric_ok(doc, row["name"]) and _effective_ok(doc, period) for doc in docs):
                group["needs_reason"] += 1
    # Roll months up into the requested closes. For months this is one-to-one.
    buckets = {}
    for month, g in groups.items():
        b = buckets.setdefault(period_key(month, granularity, fiscal_start),
                               {"g": dict.fromkeys(_COUNTS, 0), "months": [], "shared": [], "at": None, "ids": set()})
        for k in _COUNTS:
            b["g"][k] += g[k]
        b["months"].append(month)
        b["ids"] |= g.get("ids", set())
        if month in shared:
            b["shared"].append(month)
            b["at"] = max(b["at"] or "", shared[month])
    out = [{**_finish(period, b["g"], b["months"], b["shared"], b["at"]), "distinct_kpis": len(b["ids"])}
           for period, b in buckets.items()]
    for summary in out:
        covered = months_in(summary["period"], granularity, fiscal_start)
        summary.update(lock_status(conn, ds, covered))
    out.sort(key=lambda x: sort_key(x["period"], fiscal_start), reverse=True)
    return out


def _public(summary):
    return {**{k: v for k, v in summary.items() if k not in ("flagged", "months")},
            "month_count": len(summary.get("months", []))}


@router.get("/months")
def months(granularity: str = "month", _: CurrentUser = Depends(require_read)):
    if granularity not in GRANULARITIES:
        raise HTTPException(status_code=422, detail=f"granularity must be one of {GRANULARITIES}")
    conn = get_connection()
    try:
        return [_public(s) for s in _summaries(conn, active_dataset_id(conn), granularity)]
    finally:
        conn.close()


def _activity(conn, ds, period, granularity="month"):
    covered_months = set(months_in(period, granularity, dataset_metadata(conn, ds)["fiscal_year_start"]))
    reports = {
        str(r["id"]): (r["period"], r["name"])
        for r in conn.execute(
            """SELECT gr.id, gr.period, m.name FROM generated_reports gr JOIN metrics m ON m.id = gr.metric_id
           WHERE m.dataset_id = ?""",
            (ds,),
        ).fetchall()
    }
    result = []
    for entry in audit.list_entries(conn, current_workspace(), limit=2147483647):
        report = reports.get(str(entry["entity_id"])) if entry["entity_type"] == "report" else None
        in_close = bool(report) and report[0] in covered_months
        close_entry = (entry["entity_type"] == "close" and str(entry["entity_id"]) == str(ds)
                       and entry["summary"].get("period") == period)
        relevant = close_entry or in_close or (
            entry["entity_type"] == "dataset" and str(entry["entity_id"]) == str(ds)
        )
        if not relevant:
            continue
        action = {"lock": "Locked", "unlock": "Unlocked", "export": "Exported"}.get(entry["action"], entry["action"].replace("_", " "))
        text = f"{action.capitalize()} {report[1]} for {report[0]}" if report else f"{action.capitalize()} dataset"
        if close_entry:
            text = f"{action} the {_period_title(period)} close"
        result.append(
            {"at": entry["created_at"], "text": text, "actor": entry["actor_email"] or entry["actor_id"] or "System"}
        )
        if len(result) == 10:
            break
    return result


@router.get("/status")
def status(period: str, granularity: str | None = None, _: CurrentUser = Depends(require_read)):
    inferred = _granularity_of(period)
    if granularity is not None and granularity != inferred:
        raise HTTPException(status_code=422, detail="Period does not match granularity")
    granularity = inferred
    if granularity is None:
        raise HTTPException(status_code=422, detail="Period must be YYYY-MM, YYYY-Qn or YYYY")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        s = next((x for x in _summaries(conn, ds, granularity) if x["period"] == period), None)
        if s is None:
            raise HTTPException(status_code=404, detail="No facts for period")
        steps = _steps(period, s, rollup=granularity != "month")
        if granularity != "month":
            # Commentary lives on months: send each open step to the earliest
            # month that still needs it (or the latest month once it's done).
            covered_months = set(months_in(period, granularity, dataset_metadata(conn, ds)["fiscal_year_start"]))
            by_month = {m["period"]: _steps(m["period"], m) for m in _summaries(conn, ds) if m["period"] in covered_months}
            for i, st in enumerate(steps):
                todo = [m for m in sorted(by_month) if by_month[m][i]["state"] != "done"]
                target = (todo or sorted(by_month))[0 if todo else -1]
                st["target"] = f"#month={target}&tab={st['target'].rsplit('&tab=', 1)[1]}"
        covered = months_in(period, granularity, dataset_metadata(conn, ds)["fiscal_year_start"])
        return {"period": period, "label": s["label"], "steps": steps,
                "status": s["status"], "shared_at": s["shared_at"],
                **lock_status(conn, ds, covered),
                "changes_since_shared": _changes_since_shared(conn, ds, covered, s["shared_at"]),
                "activity": _activity(conn, ds, period, granularity)}
    finally:
        conn.close()


def _steps(period, s, rollup=False):
    """The close checklist for one summary; targets open that close's tabs.
    In a roll-up, counts are monthly commentaries (KPI x month), so say so."""
    remaining = s["kpis"] - s["approved"]
    pending_drafts = s["drafted"] - s["approved"]

    def n(count, one, many=None):
        return f"{count} {one if count == 1 else (many or one + 's')}"

    def step(id, title, detail, state, count, tab):
        return {
            "id": id,
            "title": title,
            "detail": detail,
            "state": state,
            "count": count,
            "target": f"#month={period}&tab={tab}",
        }

    flagged = s["drafted"] - s["checked"]  # drafts whose figures didn't all pass the check
    undrafted = s["kpis"] - s["drafted"]
    undrafted_words = ("monthly commentary is", "monthly commentaries are") if rollup else ("KPI is", "KPIs are")
    return [
        step(
            "import",
            "Import actuals and budget",
            (f"{n(s['distinct_kpis'], 'KPI', 'KPIs')} across {n(len(s['months']), 'month')} imported · open Data to check coverage"
         if rollup else f"{n(s['kpis'], 'KPI', 'KPIs')} imported · open Data to check coverage"),
            "done",
            s["kpis"],
            "data",
        ),
        step(
            "review",
            "Review the biggest movers",
            f"{n(flagged, 'draft has', 'drafts have')} a figure to check · open KPIs"
            if flagged
            else f"{n(undrafted, *undrafted_words)} not drafted yet · open KPIs"
            if undrafted
            else "Figures checked · open KPIs to review movers",
            "todo" if flagged or undrafted else "done",
            flagged + undrafted,
            "kpis",
        ),
        step(
            "reasons",
            f"Add reasons for {n(s['needs_reason'], 'variance')}"
            if s["needs_reason"]
            else "Every variance has a reason",
            f"{n(s['needs_reason'], 'variance needs', 'variances need')} a reason · open Context"
            if s["needs_reason"]
            else "Reasons recorded · open Context to review notes",
            "todo" if s["needs_reason"] else "done",
            s["needs_reason"],
            "context",
        ),
        step(
            "approve",
            f"Approve {n(pending_drafts, 'draft')}" if pending_drafts else "Approve the drafts",
            f"{n(pending_drafts, 'draft needs', 'drafts need')} approval · open KPIs"
            if pending_drafts
            else "Draft commentary on KPIs before approving"
            if remaining
            else "Drafts approved · open KPIs to read commentary",
            "done" if not remaining else "todo" if pending_drafts else "blocked",
            pending_drafts,
            "kpis",
        ),
        step(
            "share",
            "Send the digest",
            "Digest sent · open Digest to read it"
            if s["status"] == "shared"
            else "Open Digest to prepare the leadership summary"
            if not remaining
            else "Approve the drafts on KPIs before sharing",
            "done" if s["status"] == "shared" else "todo" if not remaining else "blocked",
            int(s["status"] == "shared"),
            "digest",
        ),
    ]



def _close_months(conn, ds, period, granularity):
    if granularity not in GRANULARITIES or _granularity_of(period) != granularity:
        raise HTTPException(status_code=422, detail="Period does not match granularity")
    return months_in(period, granularity, dataset_metadata(conn, ds)["fiscal_year_start"])


def _changes_since_shared(conn, ds, covered, shared_at):
    if not shared_at:
        return 0
    placeholders = ",".join("?" for _ in covered)
    rows = conn.execute(f"SELECT created_at FROM value_changes WHERE dataset_id=? AND period IN ({placeholders})",
                        (ds, *covered)).fetchall()
    shared = datetime.fromisoformat(timestamp_text(shared_at).replace("Z", "+00:00"))
    return sum(datetime.fromisoformat(timestamp_text(r["created_at"]).replace("Z", "+00:00")) > shared for r in rows)


@router.get("/changes")
def changes(period: str, granularity: str = "month", _: CurrentUser = Depends(require_read)):
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            return {"changes": [], "total": 0}
        covered = _close_months(conn, ds, period, granularity)
        return _change_rows(conn, ds, covered)
    finally:
        conn.close()


def _change_rows(conn, ds, covered):
    """The same bounded change history for JSON and Excel."""
    placeholders = ",".join("?" for _ in covered)
    where = f"vc.dataset_id=? AND vc.period IN ({placeholders})"
    args = (ds, *covered)
    total = conn.execute(f"SELECT COUNT(*) AS n FROM value_changes vc WHERE {where}", args).fetchone()["n"]
    rows = conn.execute(f"""SELECT vc.*, m.name FROM value_changes vc JOIN metrics m ON m.id=vc.metric_id
                            WHERE {where} ORDER BY vc.created_at DESC, vc.id DESC LIMIT 500""", args).fetchall()
    return {"changes": [{"metric": r["name"], "period": r["period"], "field": r["field"],
                         "old_value": r["old_value"], "new_value": r["new_value"], "source": r["source"],
                         "actor": r["actor_email"] or r["actor_id"] or "System",
                         "at": timestamp_text(r["created_at"])} for r in rows], "total": total}


class CloseSelection(BaseModel):
    period: str
    granularity: Literal["month", "quarter", "year"] = "month"


def _set_lock(payload, user, locked):
    period, granularity = payload.period, payload.granularity
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=404, detail="No active dataset")
        covered = _close_months(conn, ds, period, granularity)
        serialize_writes(conn, ds)
        summary = next((s for s in _summaries(conn, ds, granularity) if s["period"] == period), None)
        if locked and (summary is None or summary["status"] != "shared"):
            raise HTTPException(status_code=409, detail="Send the digest before locking this close.")
        ws = conn.execute("SELECT workspace_id FROM datasets WHERE id=?", (ds,)).fetchone()["workspace_id"]
        at = datetime.now(timezone.utc).isoformat()
        for month in covered:
            if locked:
                conn.execute("""INSERT INTO close_locks
                    (workspace_id, dataset_id, period, locked_at, locked_by_id, locked_by_email)
                    VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(dataset_id, period) DO NOTHING""",
                             (ws, ds, month, at, user.id, user.email))
            else:
                conn.execute("DELETE FROM close_locks WHERE dataset_id=? AND period=?", (ds, month))
        conn.commit()
        audit.record(conn, "lock" if locked else "unlock", "close", ds, actor_id=user.id,
                     actor_email=user.email, summary={"period": period})
        return {"period": period, "granularity": granularity, **lock_status(conn, ds, covered)}
    finally:
        conn.close()


@router.post("/lock")
def lock(payload: CloseSelection, user: CurrentUser = Depends(require_write)):
    return _set_lock(payload, user, True)


@router.post("/unlock")
def unlock(payload: CloseSelection, user: CurrentUser = Depends(require_write)):
    return _set_lock(payload, user, False)


@router.get("/export.xlsx", response_class=Response, responses={200: {"content": {XLSX_TYPE: {"schema": {"type": "string", "format": "binary"}}}}})
def export_close(period: str, granularity: str = "month", user: CurrentUser = Depends(require_read)):
    from app.routers.facts import build_facts

    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=404, detail="No active dataset")
        covered = _close_months(conn, ds, period, granularity)
        facts = build_facts(conn, ds, period, granularity, user, include_charts=False)
        summary = next((s for s in _summaries(conn, ds, granularity) if s["period"] == period), None)
        name = conn.execute("SELECT name FROM datasets WHERE id=?", (ds,)).fetchone()["name"]
        content = build_close_workbook(conn, ds, name, period, granularity, covered, facts, summary,
                                       _change_rows(conn, ds, covered)["changes"])
        audit.record(conn, "export", "close", ds, actor_id=user.id, actor_email=user.email, summary={"period": period})
        return Response(content, media_type=XLSX_TYPE, headers={"Content-Disposition": content_disposition(name, period)})
    finally:
        conn.close()
