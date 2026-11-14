"""Real data, kept private: words hidden from the AI provider, and a workspace's data
exported or deleted by its owner.

Hidden words (a company name, customers, people) are swapped for placeholders like
[Name 1] before anything is sent to the AI and swapped back in what it writes, so the
provider never sees them. Figures are never touched: the figure check still applies."""

from __future__ import annotations

import json
import re

MAX_TERMS = 100


def load_terms(conn, workspace_id) -> list[str]:
    if workspace_id is None:
        return []
    row = conn.execute("SELECT hidden_terms FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    try:
        terms = json.loads(row["hidden_terms"]) if row and row["hidden_terms"] else []
    except (TypeError, ValueError):
        return []
    return [t for t in terms if isinstance(t, str) and t.strip()]


def clean_terms(terms) -> list[str]:
    out, seen = [], set()
    for t in terms or []:
        t = str(t).strip()[:120]
        if len(t) >= 2 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:MAX_TERMS]


class Redactor:
    """Swap hidden words for placeholders and back. Longest terms first, whole words,
    case-insensitive; the same word always gets the same placeholder."""

    def __init__(self, terms: list[str]):
        self.terms = sorted(clean_terms(terms), key=len, reverse=True)
        self.forward = {t.lower(): f"[Name {i + 1}]" for i, t in enumerate(self.terms)}
        self.back = {v: t for t, v in zip(self.terms, self.forward.values())}
        self._re = (re.compile(r"(?<!\w)(" + "|".join(re.escape(t) for t in self.terms) + r")(?!\w)", re.I)
                    if self.terms else None)

    def hide(self, text):
        if not self._re or not isinstance(text, str):
            return text
        return self._re.sub(lambda m: self.forward[m.group(1).lower()], text)

    def show(self, text):
        if not self.terms or not isinstance(text, str):
            return text
        for placeholder, term in self.back.items():
            text = text.replace(placeholder, term)
        return text


def export_workspace(conn, workspace_id) -> dict:
    """Everything a workspace holds, as plain JSON: datasets, figures, KPI settings,
    notes, commentary with its versions and review decisions."""
    def rows(sql, args=()):
        return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # Postgres can't compare a parameter with IS; a local session has no workspace.
    ws_pred, ws_args = ("workspace_id IS NULL", ()) if workspace_id is None else ("workspace_id = ?", (workspace_id,))

    ds = rows(f"SELECT * FROM datasets WHERE {ws_pred} AND COALESCE(is_demo, false) = false", ws_args)
    ids = [d["id"] for d in ds]
    out = {"datasets": ds, "kpi_configs": [], "metrics": [], "metric_values": [], "metric_breakdowns": [],
           "generated_reports": [], "report_versions": [], "feedback": [], "close_locks": []}
    for d in ids:
        out["kpi_configs"] += rows("SELECT * FROM kpi_configs WHERE dataset_id = ?", (d,))
        out["close_locks"] += rows("SELECT * FROM close_locks WHERE dataset_id = ?", (d,))
        metrics = rows("SELECT * FROM metrics WHERE dataset_id = ?", (d,))
        out["metrics"] += metrics
        for m in metrics:
            out["metric_values"] += rows("SELECT * FROM metric_values WHERE metric_id = ?", (m["id"],))
            out["metric_breakdowns"] += rows("SELECT * FROM metric_breakdowns WHERE metric_id = ?", (m["id"],))
            reports = rows("SELECT * FROM generated_reports WHERE metric_id = ?", (m["id"],))
            out["generated_reports"] += reports
            for r in reports:
                out["report_versions"] += rows("SELECT * FROM report_versions WHERE report_id = ?", (r["id"],))
                out["feedback"] += rows("SELECT * FROM feedback WHERE report_id = ?", (r["id"],))
    out["context_documents"] = rows(f"SELECT * FROM context_documents WHERE {ws_pred} AND COALESCE(is_demo, false) = false",
                                    ws_args)
    out["connectors"] = rows(f"SELECT id, kind, name, dataset_name, last_sync_at, last_status FROM connectors WHERE {ws_pred}",
                             ws_args)
    return out
