"""The team's house style for commentary: wording, length and forward-looking lines.

Stored per workspace and turned into prompt rules. It changes words only: figures are
still copied from the computed facts and checked."""

from __future__ import annotations

import json

DEFAULT_STYLE = {"favourable_wording": False, "max_sentences": 3, "forward_looking": True, "instruction": ""}


def clean_style(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_STYLE)
    out["favourable_wording"] = bool(raw.get("favourable_wording", out["favourable_wording"]))
    try:
        out["max_sentences"] = min(max(int(raw.get("max_sentences", out["max_sentences"])), 1), 6)
    except (TypeError, ValueError):
        pass
    out["forward_looking"] = bool(raw.get("forward_looking", out["forward_looking"]))
    out["instruction"] = str(raw.get("instruction") or "").strip()[:300]
    return out


def load_style(conn, workspace_id) -> dict | None:
    if workspace_id is None:
        return None
    row = conn.execute("SELECT house_style FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    try:
        return clean_style(json.loads(row["house_style"])) if row and row["house_style"] else None
    except (TypeError, ValueError):
        return None


def style_rules(style: dict | None) -> list[str]:
    if not style:
        return []
    rules = [f"Write at most {style['max_sentences']} sentences."]
    if style["favourable_wording"]:
        rules.append('Describe variances as "favourable" or "unfavourable" to plan, not "over" or "exceeding".')
    if not style["forward_looking"]:
        rules.append("Do not include any forward-looking or what-to-watch sentence.")
    if style["instruction"]:
        rules.append(f"House style: {style['instruction']}")
    return rules
