"""Board pack (v5.1): assemble the month's KPIs, deltas, trends and narratives
into ONE self-contained, print-ready HTML document — the artifact an FP&A team
actually sends to leadership.

Everything is inlined (CSS + SVG sparklines, no JS, no external assets) so the
page renders identically when saved as a file, emailed, or printed to PDF. The
builder is a pure function of already-computed facts, so it's trivially testable
and reuses the exact numbers shown on the dashboard.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone


def _fmt(v: float | None, unit: str | None) -> str:
    """Finance formatting, mirroring the frontend's fmt(): sign before the
    currency symbol, K/M magnitudes, one-dp percents."""
    if v is None:
        return "–"
    if unit == "%":
        return f"{round(v, 1):g}%"
    if unit == "count":
        return f"{round(v):,}"
    a, sign = abs(v), "-" if v < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}${a / 1_000:.0f}K"
    return f"{sign}${a:,.2f}"


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
        f'<polyline points="{actual}" fill="none" stroke="#1e6e50" stroke-width="1.8" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last[0]}" cy="{last[1]}" r="2.4" fill="#1e6e50"/>'
        f'</svg>'
    )


_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Plus Jakarta Sans',system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  color:#16211b;background:#eef1ee;line-height:1.5;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.pack{max-width:900px;margin:0 auto;background:#fff;padding:44px 48px 56px}
.masthead{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #1e6e50;padding-bottom:18px;margin-bottom:26px}
.brand{font-size:13px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:#1e6e50}
h1{font-size:27px;font-weight:800;margin:6px 0 3px;letter-spacing:-.01em}
.sub{font-size:13px;color:#5c6a62;font-weight:600}
.meta{text-align:right;font-size:11.5px;color:#75837a;font-weight:600;line-height:1.7}
.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:30px}
.tile{border:1px solid #e4e8e4;border-radius:12px;padding:15px 17px;background:#fafbfa;position:relative;overflow:hidden}
.tile::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:#1e6e50}
.tile .k{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#5c6a62}
.tile .v{font-size:23px;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums}
.tile .n{font-size:11.5px;color:#75837a;margin-top:2px}
.section-label{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;color:#75837a;margin:28px 0 12px}
.kpi{display:grid;grid-template-columns:1fr auto;gap:8px 20px;padding:16px 0;border-top:1px solid #edf0ec;break-inside:avoid;page-break-inside:avoid}
.kpi-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.kpi-name{font-size:16px;font-weight:800}
.cat{font-size:10.5px;font-weight:700;color:#5c6a62;background:#eef1ee;border-radius:6px;padding:2px 7px;
  text-transform:uppercase;letter-spacing:.04em}
.anom{font-size:10.5px;font-weight:800;color:#b42e2c;background:#faeeed;border:1px solid #f0c9c7;border-radius:6px;
  padding:2px 7px;text-transform:uppercase;letter-spacing:.04em}
.kpi-val{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.chip{font-size:11.5px;font-weight:700;border-radius:7px;padding:3px 9px;font-variant-numeric:tabular-nums}
.chip .lbl{font-weight:600;color:#5c6a62;margin-right:4px}
.chip.good{background:#e9f3ee;color:#1e6e50} .chip.bad{background:#faeeed;color:#b42e2c} .chip.flat{background:#eef1ee;color:#5c6a62}
.narr{grid-column:1/-1;font-size:13.5px;color:#33413a;margin-top:8px;max-width:64ch}
.narr.muted{color:#9aa79f;font-style:italic}
.spark{display:block;margin-top:4px}
.foot{margin-top:34px;border-top:1px solid #edf0ec;padding-top:14px;font-size:11px;color:#9aa79f;display:flex;justify-content:space-between}
@media print{body{background:#fff}.pack{max-width:none;padding:0 8px}.no-print{display:none}@page{margin:16mm}}
"""


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
               WHERE metric_id = ? AND period = ?
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
    top_txt = (f"{e(top['metric'])} {_pct((top['deltas'] or {}).get('budget_var_pct'))} vs plan"
               if top else "–")

    ds_name = e(str(meta.get("dataset_name") or "Dataset"))
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
            f'<span class="chip {_delta_class(d.get("mom_pct"), dg)}"><span class="lbl">MoM</span>{_pct(d.get("mom_pct"))}</span>'
            f'<span class="chip {_delta_class(d.get("budget_var_pct"), dg)}"><span class="lbl">vs plan</span>{_pct(d.get("budget_var_pct"))}</span>'
        )
        if d.get("yoy_pct") is not None:
            chips += f'<span class="chip {_delta_class(d.get("yoy_pct"), dg)}"><span class="lbl">YoY</span>{_pct(d.get("yoy_pct"))}</span>'
        spark = _sparkline((f.get("chart_data") or {}).get("trend", []))
        narrative = f.get("narrative")
        narr = (f'<div class="narr">{e(narrative)}</div>' if narrative
                else '<div class="narr muted">No narrative generated for this metric.</div>')
        anom = '<span class="anom">Anomaly</span>' if f.get("is_anomaly") else ""
        rows.append(
            f'<div class="kpi"><div>'
            f'<div class="kpi-head"><span class="kpi-name">{e(f["metric"])}</span>'
            f'<span class="cat">{e(f.get("category") or "")}</span>{anom}</div>'
            f'<div class="chips">{chips}</div></div>'
            f'<div><div class="kpi-val">{_fmt(f.get("value"), unit)}</div>{spark}</div>'
            f'{narr}</div>'
        )
    body = "".join(rows) or '<div class="narr muted">No KPIs with data for this period.</div>'

    ws_line = f'<div>{e(str(ws_name))}</div>' if ws_name else ""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Board Pack — {e(ds_name)} — {e(period)}</title>"
        f"<style>{_CSS}</style></head><body><div class=\"pack\">"
        f'<div class="masthead"><div>'
        f'<div class="brand">Closebrief</div>'
        f'<h1>Board Pack</h1>'
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
