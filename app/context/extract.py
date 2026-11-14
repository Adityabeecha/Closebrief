"""Turn pasted text (an earnings release, MD&A, a variance memo, an email) into proposed
context notes: one per KPI it explains, tagged with the dataset's own KPI names and the
period it covers. The analyst ticks the ones to keep; nothing is saved here.

The model only sorts and quotes the analyst's text: every note body must be taken from
the pasted source, and a KPI name it returns must be one of the dataset's KPIs."""

from __future__ import annotations

import json
import re

MAX_SOURCE_CHARS = 30_000
MAX_NOTES = 20

EXTRACT_SYSTEM_PROMPT = """You split an FP&A source document into context notes for month-end commentary.
Return ONLY a JSON array in the "narrative" field, no prose. Each item:
{"kpi": one name copied exactly from the KPI list, "title": a short title (max 12 words),
 "body": the explanation, quoted or lightly trimmed from the source, never invented,
 "period": "YYYY-MM" the month (or quarter-end month) it explains,
 "recurring": true only if the source says it happens every period (e.g. stock-based compensation
 is always the GAAP to non-GAAP gap)}
Rules: one item per KPI per period; skip KPIs the source doesn't explain; never add a cause the
source doesn't state; keep the source's figures exactly as written; if nothing applies return []."""


def build_prompt(text: str, kpis: list[str], default_period: str | None) -> str:
    return (
        f"KPI list: {json.dumps(kpis)}\n"
        f"Period to use when the source doesn't say: {default_period or 'unknown'}\n\n"
        f"Source:\n{text[:MAX_SOURCE_CHARS]}"
    )


def parse_proposals(raw: str, kpis: list[str], default_period: str | None) -> list[dict]:
    """Keep only well-formed proposals for known KPIs; tolerate a fenced or wrapped array."""
    match = re.search(r"\[.*\]", raw or "", re.S)
    try:
        items = json.loads(match.group(0)) if match else []
    except (json.JSONDecodeError, AttributeError):
        return []
    by_lower = {k.strip().lower(): k for k in kpis}
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        kpi = by_lower.get(str(it.get("kpi", "")).strip().lower())
        body = str(it.get("body") or "").strip()
        period = str(it.get("period") or default_period or "")[:7]
        if not kpi or not body or not re.fullmatch(r"\d{4}-\d{2}", period):
            continue
        if (kpi, period) in seen:
            continue
        seen.add((kpi, period))
        out.append({
            "kpi": kpi,
            "title": (str(it.get("title") or "").strip() or f"Why {kpi} moved")[:200],
            "body": body[:5000],
            "period": period,
            "recurring": bool(it.get("recurring")),
        })
        if len(out) >= MAX_NOTES:
            break
    return out
