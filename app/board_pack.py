"""Board pack (v5.1): assemble the month's KPIs, deltas, trends and narratives
into ONE self-contained, print-ready HTML document — the artifact an FP&A team
actually sends to leadership.

Everything is inlined (CSS + SVG sparklines, no JS, no external assets) so the
page renders identically when saved as a file, emailed, or printed to PDF. The
builder is a pure function of already-computed facts, so it's trivially testable
and reuses the exact numbers shown on the dashboard.
"""

from __future__ import annotations

import base64
import calendar
import html
import re
from datetime import datetime, timezone
from pathlib import Path

from app.currency import symbol


def dataset_display_name(name: str) -> str:
    """Present filenames as names while preserving deliberately named datasets."""
    if not re.search(r"\.(csv|xlsx)$", name, re.IGNORECASE):
        return name
    words = re.sub(r"[_-]+", " ", re.sub(r"\.(csv|xlsx)$", "", name, flags=re.IGNORECASE)).strip()
    return words.lower().capitalize()


def _fmt(v: float | None, unit: str | None, currency: str | None = "USD") -> str:
    """Finance formatting, mirroring the frontend's fmt(): sign before the
    currency symbol, K/M magnitudes, one-dp percents."""
    if v is None:
        return "–"
    if unit == "%":
        return f"{round(v, 1):g}%"
    if unit == "count":
        return f"{round(v):,}"
    if unit == "number":
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if unit == "days":
        return f"{v:,.1f}".rstrip("0").rstrip(".") + (" day" if abs(v) == 1 else " days")
    a, sign, mark = abs(v), "-" if v < 0 else "", symbol(currency)
    if a >= 1_000_000:
        return f"{sign}{mark}{a / 1_000_000:.2f}M"
    if a >= 100_000:
        return f"{sign}{mark}{a / 1_000:.0f}K"
    if a >= 1_000:
        return f"{sign}{mark}{a:,.0f}"   # in full under 100K, as on screen
    return f"{sign}{mark}{a:,.0f}" if currency == "JPY" else f"{sign}{mark}{a:,.2f}"


def _delta(f: dict, key: str) -> str:
    """A variance as written on the pack: points for a percent KPI (38% vs a 40%
    plan is -2.0 pts), the relative change otherwise."""
    d = f.get("deltas") or {}
    rel = d.get({"budget": "budget_var_pct", "mom": "mom_pct", "yoy": "yoy_pct"}[key])
    if rel is None:
        return "–"
    unit = (f.get("unit") or "").lower()
    if unit not in ("%", "percent", "days"):
        return _pct(rel)
    value = f.get("value")
    if key == "budget":
        pts = d.get("budget_var_abs")
    elif key == "mom":
        pts = None if value is None or f.get("prior_value") is None else value - f["prior_value"]
    else:
        pts = None if value is None or rel == -100 else value - value / (1 + rel / 100)
    return "–" if pts is None else f"{'+' if pts >= 0 else ''}{round(pts, 1):.1f} {'days' if unit == 'days' else 'pts'}"


def _pct(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{'+' if v >= 0 else ''}{round(v, 1):g}%"


def _delta_class(v: float | None, direction_good: str) -> str:
    """good/bad colour for a delta, honouring whether up or down is good."""
    if v is None or v == 0:
        return "flat"
    good = (v > 0) if direction_good != "down" else (v < 0)
    return "good" if good else "bad"


def _sparkline(trend: list[dict], w: int = 132, h: int = 34) -> str:
    """Inline SVG sparkline of the actual series, with a faint budget line when
    present. No axes — a glanceable shape, print-safe. `trend` is oldest→newest,
    matching the /facts chart_data contract."""
    pts = [t for t in (trend or []) if t.get("value") is not None]
    if len(pts) < 2:
        return ""
    vals = [float(t["value"]) for t in pts]
    buds = [t.get("budget") for t in pts]
    lo = min([v for v in vals] + [float(b) for b in buds if b is not None])
    hi = max([v for v in vals] + [float(b) for b in buds if b is not None])
    span = (hi - lo) or 1.0
    pad = 3

    def xy(i, val):
        x = pad + i * (w - 2 * pad) / (len(pts) - 1)
        y = pad + (h - 2 * pad) * (1 - (float(val) - lo) / span)
        return f"{x:.1f},{y:.1f}"

    actual = " ".join(xy(i, v) for i, v in enumerate(vals))
    budget_pts = [(i, b) for i, b in enumerate(buds) if b is not None]
    budget = ""
    if len(budget_pts) >= 2:
        bpath = " ".join(xy(i, b) for i, b in budget_pts)
        budget = (f'<polyline points="{bpath}" fill="none" stroke="#9aa79f" '
                  f'stroke-width="1" stroke-dasharray="3,3" opacity="0.8"/>')
    last = xy(len(vals) - 1, vals[-1]).split(",")
    return (
        f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="trend">'
        f'{budget}'
        f'<polyline points="{actual}" fill="none" stroke="#047857" stroke-width="1.8" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last[0]}" cy="{last[1]}" r="2.4" fill="#047857"/>'
        f'</svg>'
    )


_ASSETS = Path(__file__).resolve().parent.parent / "ui" / "web"


def _assets_css():
    return "".join(
        f"@font-face{{font-family:'{family}';src:url(data:font/woff2;base64,{base64.b64encode((_ASSETS / 'vendor/fonts' / filename).read_bytes()).decode()}) format('woff2');font-weight:100 900}}"
        for family, filename in (("Schibsted Grotesk", "schibsted-grotesk.woff2"), ("Red Hat Mono", "redhat-mono.woff2"))
    )


_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Schibsted Grotesk',system-ui,sans-serif;color:#0b1220;background:#fbfcfd;line-height:1.55;print-color-adjust:exact;-webkit-print-color-adjust:exact}
.pack{max-width:900px;margin:auto;padding:48px;background:white}
.masthead{display:flex;justify-content:space-between;gap:24px;padding-bottom:40px;border-bottom:1px solid #e6e9ef;margin-bottom:32px}
.brand{display:flex;align-items:center;gap:12px;font-size:28px;font-weight:800}.brand svg{width:48px;height:48px}.brand span{font-weight:500}
h1{font-size:38px;letter-spacing:-.035em;line-height:1.1;margin:36px 0 16px}.sub,.meta,.foot{color:#596579;font-size:12px}.meta{text-align:right}.summary{display:flex;gap:16px;margin-bottom:40px}.tile{flex:1;border:1px solid #e6e9ef;border-radius:12px;padding:16px}.k,.section-label{font-size:12px;font-weight:700;color:#344054}.v,.kpi-val,.chip,.meta,.foot,mark,.pill{font-family:'Red Hat Mono',monospace;font-variant-numeric:tabular-nums}.v{font-size:24px}.n{font-size:12px;color:#596579}.kpi{border:1px solid #e6e9ef;border-radius:16px;padding:28px;margin:24px 0;break-inside:avoid}.kpi-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.kpi-name{font-size:28px;font-weight:750;letter-spacing:-.03em}.cat{font-size:12px;color:#596579}.anom{font-size:12px;color:#c8281c;background:#fdeeed;border-radius:999px;padding:4px 10px}.kpi-val{font-size:36px;margin:24px 0 12px}.chips{display:flex;gap:12px;flex-wrap:wrap}.chip{padding:5px 10px;border-radius:8px;font-size:14px}.lbl{font-family:'Schibsted Grotesk',sans-serif;margin-right:6px}.good,mark,.pill{color:#067647;background:#e8f8ef}.bad{color:#c8281c;background:#fdeeed}.flat{color:#344054;background:#f1f4f8}.narr{font-size:18px;line-height:1.75;margin:24px 0;max-width:40em}.muted{color:#596579}mark{padding:1px 4px;border-radius:4px}.pill{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px}.spark{margin-top:12px}.foot{border-top:1px solid #e6e9ef;padding-top:20px;display:flex;justify-content:space-between}
@media print{@page{size:A4 portrait;margin:18mm}body{background:white}.pack{padding:0;max-width:none}.masthead{min-height:205mm;break-after:page;display:block}.meta{text-align:left;margin-top:40px}.kpi{break-before:page;break-inside:avoid;margin:0;border:0;padding:0}.no-print{display:none}}
"""


def _checked_narrative(text, fact):
    from app.generation.guard import _NUMBER_RE, _allowed_values, _matches_any, _normalize
    from app.schemas import ComputedFact
    model = ComputedFact(metric=fact['metric'], period=fact.get('period', ''),
                         value=fact['value'], unit=fact.get('unit') or 'USD',
                         prior_value=fact.get('prior_value'), deltas=fact.get('deltas') or {})
    magnitudes, percents = _allowed_values(model)
    parts, cursor, checked, total = [], 0, 0, 0
    for match in _NUMBER_RE.finditer(text):
        parts.append(html.escape(text[cursor:match.start()]))
        token = match.group().rstrip()
        norm = _normalize(*match.groups())
        total += 1
        ok = norm and _matches_any(norm[0], percents if norm[1] else magnitudes, .02)
        checked += bool(ok)
        parts.append(f'<mark>{html.escape(token)}</mark>' if ok else html.escape(token))
        parts.append(html.escape(match.group()[len(token):]))
        cursor = match.end()
    parts.append(html.escape(text[cursor:]))
    return ''.join(parts), checked, total



def collect_facts(conn, dataset_id: int, period: str, trend_window: int = 12) -> list[dict]:
    """Assemble board-pack fact dicts for a dataset/period straight from the store
    (no request/user needed) — value, deltas, latest narrative, and a trend series
    for the sparkline. Shared by the /board-pack endpoint's scheduler counterpart
    so on-demand and scheduled packs render identically. Movers first."""
    rows = conn.execute(
        """SELECT m.id AS metric_id, m.name AS metric, m.category, m.unit, m.direction_good,
                  cf.value, cf.prior_value, cf.mom_pct, cf.yoy_pct,
                  cf.budget_var_abs, cf.budget_var_pct, cf.is_anomaly
           FROM metrics m JOIN computed_facts cf ON cf.metric_id = m.id AND cf.period = ?
           WHERE m.dataset_id = ?
           ORDER BY ABS(COALESCE(cf.budget_var_abs, 0)) DESC, m.name""",
        (period, dataset_id),
    ).fetchall()
    out = []
    for r in rows:
        rep = conn.execute(
            """SELECT narrative FROM generated_reports
               WHERE metric_id = ? AND period = ? AND review_status = 'approved'
                 AND (prompt_version IS NULL OR prompt_version NOT LIKE '%-digest')
               ORDER BY id DESC LIMIT 1""",
            (r["metric_id"], period),
        ).fetchone()
        trend = [
            {"period": t["period"], "value": t["value"], "budget": t["budget"]}
            for t in reversed(conn.execute(
                """SELECT period, value, budget FROM metric_values
                   WHERE metric_id = ? AND period <= ? ORDER BY period DESC LIMIT ?""",
                (r["metric_id"], period, trend_window)).fetchall())
        ]
        out.append({
            "metric": r["metric"], "category": r["category"], "period": period,
            "value": r["value"], "unit": r["unit"], "direction_good": r["direction_good"],
            "has_data": r["value"] is not None, "is_anomaly": bool(r["is_anomaly"]),
            "narrative": rep["narrative"] if rep else None,
            "deltas": {"mom_pct": r["mom_pct"], "yoy_pct": r["yoy_pct"],
                       "budget_var_abs": r["budget_var_abs"], "budget_var_pct": r["budget_var_pct"]},
            "chart_data": {"trend": trend},
        })
    return out


def _period_title(period: str) -> str:
    """'2025-12' -> 'December 2025', '2025-Q4' -> 'Q4 2025', '2025' -> '2025'."""
    m = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", period or "")
    if m:
        return f"{calendar.month_name[int(m.group(2))]} {m.group(1)}"
    m = re.fullmatch(r"(\d{4})-Q([1-4])", period or "")
    if m:
        return f"Q{m.group(2)} {m.group(1)}"
    return period or ""


def build_board_pack_html(facts: list[dict], period: str, meta: dict | None = None) -> str:
    """Render the board pack. `facts` is the /facts payload (dicts with metric,
    value, unit, deltas, narrative, is_anomaly, chart_data). `meta` may carry
    dataset_name, workspace_name."""
    meta = meta or {}
    e = html.escape
    with_data = [f for f in facts if f.get("has_data")]
    anomalies = [f for f in with_data if f.get("is_anomaly")]
    # Top movers = largest absolute budget variance (already how /facts sorts).
    def _absvar(f):
        v = (f.get("deltas") or {}).get("budget_var_abs")
        return abs(v) if v is not None else -1.0
    movers = sorted(with_data, key=_absvar, reverse=True)
    top = next((m for m in movers if _absvar(m) >= 0), None)
    top_txt = (f"{e(top['metric'])} {_delta(top, 'budget')} vs plan"
               if top else "–")

    ds_name = e(dataset_display_name(str(meta.get("dataset_name") or "Dataset")))
    ws_name = meta.get("workspace_name")
    generated = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    tiles = (
        f'<div class="tile"><div class="k">KPIs reported</div><div class="v">{len(with_data)}</div>'
        f'<div class="n">for {e(period)}</div></div>'
        f'<div class="tile"><div class="k">Anomalies</div><div class="v">{len(anomalies)}</div>'
        f'<div class="n">{"flagged this period" if anomalies else "none flagged"}</div></div>'
        f'<div class="tile"><div class="k">Largest variance</div><div class="v" style="font-size:15px">{top_txt}</div>'
        f'<div class="n">biggest swing vs plan</div></div>'
    )

    rows = []
    for f in with_data:
        d = f.get("deltas") or {}
        dg = f.get("direction_good") or "up"
        unit = f.get("unit")
        chips = (
            f'<span class="chip {_delta_class(d.get("mom_pct"), dg)}"><span class="lbl">vs prior period</span>{_delta(f, "mom")}</span>'
            f'<span class="chip {_delta_class(d.get("budget_var_pct"), dg)}"><span class="lbl">vs plan</span>{_delta(f, "budget")}</span>'
        )
        if d.get("yoy_pct") is not None:
            chips += f'<span class="chip {_delta_class(d.get("yoy_pct"), dg)}"><span class="lbl">YoY</span>{_delta(f, "yoy")}</span>'
        spark = _sparkline((f.get("chart_data") or {}).get("trend", []))
        narrative = f.get("narrative")
        checked_text, checked, total = _checked_narrative(narrative or '', f)
        pill = f'<span class="pill">{checked} of {total} figures checked</span>' if narrative else ''
        narr = (f'<div class="narr">{checked_text}</div>{pill}' if narrative
                else '<div class="narr muted">No narrative generated for this metric.</div>')
        anom = '<span class="anom">Anomaly</span>' if f.get("is_anomaly") else ""
        rows.append(
            f'<div class="kpi"><div>'
            f'<div class="kpi-head"><span class="kpi-name">{e(f["metric"])}</span>'
            f'<span class="cat">{e(f.get("category") or "")}</span>{anom}</div>'
            f'<div class="chips">{chips}</div></div>'
            f'<div><div class="k">Actual</div><div class="kpi-val">{_fmt(f.get("value"), unit, f.get("currency"))}</div>{spark}</div>'
            f'{narr}</div>'
        )
    body = "".join(rows) or '<div class="narr muted">No KPIs with data for this period.</div>'

    logo = (_ASSETS / "brand/logo-mark.svg").read_text(encoding="utf-8").replace(' xmlns="http://www.w3.org/2000/svg"', '')
    ws_line = f'<div>{e(str(ws_name))}</div>' if ws_name else ""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Board Pack — {ds_name} — {e(period)}</title>"
        f"<style>{_assets_css()}{_CSS}</style></head><body><div class=\"pack\">"
        f'<div class="masthead"><div>'
        f'<div class="brand">{logo}<div>close<span>brief</span></div></div>'
        f'<h1>{e(_period_title(period))}<br>Board Pack</h1><p>Every figure computed in code and checked</p>'
        f'<div class="sub">{ds_name} · {e(period)}</div></div>'
        f'<div class="meta">{ws_line}<div>Generated {generated}</div>'
        f'<div>Deterministic figures · grounded narratives</div></div></div>'
        f'<div class="summary">{tiles}</div>'
        f'<div class="section-label">KPI detail — ordered by variance vs plan</div>'
        f'{body}'
        f'<div class="foot"><span>Closebrief — narrative FP&amp;A</span>'
        f'<span>{e(period)}</span></div>'
        f"</div></body></html>"
    )
