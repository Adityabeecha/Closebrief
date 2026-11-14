"""A reason column in the import ("Comment", "Variance explanation") becomes context notes.

Many teams already write a comment next to each line's actual and budget. Each KPI and
period's comments become one note, tagged with the KPI and dated in that period, so the
commentary can cite them without anyone retyping them."""

from __future__ import annotations

import pandas as pd

TITLE_PREFIX = "From the import:"


def comments_from_frame(df: pd.DataFrame, mapping) -> dict[tuple[str, str], str]:
    """{(metric, 'YYYY-MM'): text} from a long file's comment column."""
    from app.ingestion.mapping import parse_period

    col = getattr(mapping, "comment_col", None)
    if not col or col not in df.columns or mapping.layout != "long" or not mapping.metric_col:
        return {}
    out: dict[tuple[str, str], list[str]] = {}
    dims = [c for c in (mapping.dimension_cols or []) if c in df.columns]
    for _, row in df.iterrows():
        text = row.get(col)
        if text is None or (isinstance(text, float) and pd.isna(text)) or not str(text).strip():
            continue
        try:
            period = parse_period(mapping.period_literal) if mapping.period_literal else parse_period(row[mapping.period_col])
        except Exception:  # noqa: BLE001 - a row the import itself would skip
            continue
        metric = str(row[mapping.metric_col]).strip()
        part = " / ".join(str(row[d]).strip() for d in dims if str(row[d]).strip())
        line = f"{part}: {str(text).strip()}" if part else str(text).strip()
        bucket = out.setdefault((metric, str(period)[:7]), [])
        if line not in bucket:
            bucket.append(line)
    return {k: " ".join(v)[:5000] for k, v in out.items()}


def store_comment_notes(conn, store, dataset_id: int, comments: dict, filename: str) -> int:
    """Replace this dataset's earlier import comments for the same KPI and period."""
    from app.schemas import ContextDocIn

    added = 0
    for (metric, period), text in comments.items():
        title = f"{TITLE_PREFIX} {metric}, {period}"
        for r in conn.execute(
                "SELECT id FROM context_documents WHERE dataset_id = ? AND title = ?", (dataset_id, title)).fetchall():
            store.delete(r["id"])
        store.add(ContextDocIn(type="event_note", title=title, body=text, metric_tags=[metric],
                               effective_date=period, scope="dataset"), dataset_id=dataset_id)
        added += 1
    return added
