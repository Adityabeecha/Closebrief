# ruff: noqa: E501  (the review page is one HTML template)
"""A manager or controller approves a close from a link, without an account.

The analyst creates a signed link for one close (POST /close/approval-link). Whoever
opens it sees that close's drafts with their figures and can approve them or request
changes with a note; nothing else in the workspace is reachable. The link expires after
7 days, and every decision is recorded with the name the reviewer gives and "via link".
"""

from __future__ import annotations

import html
import json
import time

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from app import audit
from app import review as review_svc
from app.api import require_write  # noqa: E402
from app.auth import CurrentUser
from app.close_control import ensure_report_unlocked
from app.config import session_secret, settings
from app.datasets import active_dataset_id, current_workspace, set_demo_scope, set_workspace_scope
from app.db import get_connection

router = APIRouter()

LINK_DAYS = 7
_AUD = "closebrief-approval"


def _secret() -> str:
    secret = session_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Approval links need a signing secret on the server.")
    return secret


def make_token(workspace_id, dataset_id: int, period: str, by: str) -> tuple[str, int]:
    exp = int(time.time()) + LINK_DAYS * 86400
    token = jwt.encode({"aud": _AUD, "ws": workspace_id, "ds": dataset_id, "p": period, "by": by, "exp": exp},
                       _secret(), algorithm="HS256")
    return token, exp


def read_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"], audience=_AUD)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=410, detail="This approval link has expired. Ask for a new one.") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=404, detail="This approval link isn't valid.") from exc


def _drafts(conn, claims: dict) -> list[dict]:
    rows = conn.execute(
        """SELECT gr.id, m.name AS metric, COALESCE(k.unit, m.unit) AS unit, gr.narrative, gr.review_status,
                  gr.faithfulness, cf.value, cf.budget_var_pct, cf.budget_var_abs
           FROM generated_reports gr JOIN metrics m ON m.id = gr.metric_id
           LEFT JOIN kpi_configs k ON k.dataset_id = m.dataset_id AND k.source_metric = m.name
           LEFT JOIN computed_facts cf ON cf.metric_id = m.id AND cf.period = gr.period
           WHERE m.dataset_id = ? AND gr.period = ?
             AND (gr.prompt_version IS NULL OR gr.prompt_version NOT LIKE '%-digest')
           ORDER BY gr.id ASC""", (claims["ds"], claims["p"])).fetchall()
    latest = {}
    for r in rows:
        latest[r["metric"]] = dict(r)   # ascending ids: the newest draft per KPI wins
    for d in latest.values():
        edited = conn.execute("""SELECT edited_text FROM feedback WHERE report_id = ? AND action = 'edited'
                                 AND edited_text IS NOT NULL ORDER BY id DESC LIMIT 1""", (d["id"],)).fetchone()
        if edited:
            d["narrative"] = edited["edited_text"]
    return sorted(latest.values(), key=lambda d: -abs(d["budget_var_abs"] or 0))


def _scope(claims: dict) -> None:
    set_demo_scope(False)
    set_workspace_scope(claims.get("ws"))


@router.post("/close/approval-link")
def approval_link(payload: dict, user: CurrentUser = Depends(require_write)) -> dict:
    period = str(payload.get("period") or "")
    if len(period) != 7:
        raise HTTPException(status_code=422, detail="Approval links are for a month's close (YYYY-MM).")
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        if ds is None:
            raise HTTPException(status_code=409, detail="No dataset open")
        token, exp = make_token(current_workspace(), ds, period, user.email or user.id)
        audit.record(conn, "approval_link", "close", ds, actor_id=user.id, actor_email=user.email,
                     summary={"period": period, "days": LINK_DAYS})
    finally:
        conn.close()
    return {"url": f"{settings.app_base_url.rstrip('/')}/approve?t={token}", "expires_at": exp, "days": LINK_DAYS}


@router.get("/approve", response_class=HTMLResponse)
def approval_page(t: str) -> HTMLResponse:
    claims = read_token(t)
    _scope(claims)
    conn = get_connection()
    try:
        name = conn.execute("SELECT name FROM datasets WHERE id = ?", (claims["ds"],)).fetchone()
        drafts = _drafts(conn, claims)
    finally:
        conn.close()
    e = html.escape
    items = "".join(
        f'''<article data-metric="{e(d["metric"])}"><h3>{e(d["metric"])}<span class="st {e(d["review_status"] or "pending")}">
        {"Approved" if d["review_status"] == "approved" else "Changes requested" if d["review_status"] == "changes_requested" else "Waiting for you"}</span></h3>
        <p class="fig">{e(_fmt(d["value"], d["unit"]))}{f" · {d['budget_var_pct']:+.1f}% vs plan" if d["budget_var_pct"] is not None and d["unit"] != "%" else ""}
        {" · figures checked" if d["faithfulness"] == "passed" else " · figures need review"}</p>
        <p>{e(d["narrative"] or "")}</p>
        <div class="act"><button onclick="decide(this,'approved')">Approve</button>
        <button class="ghost" onclick="decide(this,'changes_requested')">Request changes</button></div></article>'''
        for d in drafts) or "<p>No commentary has been drafted for this close yet.</p>"
    title = f"{e(name['name'] if name else 'Close')} · {e(claims['p'])}"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Approve commentary · {title}</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;max-width:760px;margin:0 auto;padding:24px 16px;color:#1d2420;background:#fbfbf8}}
h1{{font-size:22px;margin:0 0 4px}}.sub{{color:#5c665f;margin:0 0 20px}}article{{background:#fff;border:1px solid #e3e6e1;border-radius:12px;padding:14px 16px;margin-bottom:12px}}
h3{{margin:0 0 4px;font-size:16px;display:flex;justify-content:space-between;gap:8px}}.st{{font-size:12px;font-weight:600;color:#7a5a00}}.st.approved{{color:#1f7a45}}
.fig{{color:#5c665f;font-size:13px;margin:0 0 8px}}.act{{display:flex;gap:8px}}button{{font:inherit;padding:7px 12px;border-radius:8px;border:1px solid #1f7a45;background:#1f7a45;color:#fff;cursor:pointer}}
button.ghost{{background:#fff;color:#1f7a45}}label{{display:block;margin:0 0 16px;font-size:14px}}input{{font:inherit;padding:7px;border:1px solid #ccc;border-radius:8px;width:260px}}</style></head>
<body><h1>Approve commentary</h1><p class="sub">{title} · shared by {e(str(claims.get('by') or ''))}. Figures are computed in code; check the words.</p>
<label>Your name <input id="who" placeholder="e.g. Priya, FP&amp;A manager" autocomplete="name"></label>{items}
<script>const T={json.dumps(t)};
async function decide(btn,action){{const who=document.getElementById('who').value.trim();if(!who){{alert('Add your name first');return;}}
 let note=null;if(action==='changes_requested'){{note=prompt('What should change?');if(!note)return;}}
 const art=btn.closest('article');btn.disabled=true;
 const r=await fetch('/approve',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{t:T,name:who,metric:art.dataset.metric,action,note}})}});
 const st=art.querySelector('.st');if(r.ok){{st.textContent=action==='approved'?'Approved':'Changes requested';st.className='st '+action;}}else{{st.textContent=(await r.json()).detail||'Failed';}}btn.disabled=false;}}</script></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/approve")
def approval_decision(payload: dict) -> dict:
    claims = read_token(str(payload.get("t") or ""))
    action = payload.get("action")
    who = str(payload.get("name") or "").strip()[:120]
    if action not in ("approved", "changes_requested"):
        raise HTTPException(status_code=422, detail="action must be approved or changes_requested")
    if not who:
        raise HTTPException(status_code=422, detail="Add your name")
    note = str(payload.get("note") or "").strip()[:2000] or None
    _scope(claims)
    conn = get_connection()
    try:
        draft = next((d for d in _drafts(conn, claims) if d["metric"] == payload.get("metric")), None)
        if draft is None:
            raise HTTPException(status_code=404, detail="No draft for that KPI in this close")
        ensure_report_unlocked(conn, draft["id"])
        review_svc.set_status(conn, draft["id"], action)
        if note:
            conn.execute("INSERT INTO feedback (report_id, action, reason, user_email) VALUES (?, 'rejected', ?, ?)",
                         (draft["id"], note, f"{who} (via link)"))
            conn.commit()
        audit.record(conn, action, "report", draft["id"], actor_email=f"{who} (via approval link)",
                     summary={"note": note, "period": claims["p"]})
    finally:
        conn.close()
    return {"metric": draft["metric"], "review_status": action}


def _fmt(v, unit) -> str:
    if v is None:
        return "–"
    u = (unit or "USD").lower()
    if u in ("%", "percent"):
        return f"{v:.1f}%"
    if u in ("count", "number"):
        return f"{v:,.0f}"
    if u == "days":
        return f"{v:.0f} days"
    a = abs(v)
    s = f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.2f}M" if a >= 1e6 else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:,.0f}"
    return ("-" if v < 0 else "") + s
