"""When a context note counts as the recorded reason for a KPI's month."""


def reason_recorded(doc, metric: str, month: str) -> bool:
    """A note explains this KPI's month only when it names the KPI and is either dated
    in that month or an undated event note. An untagged note ("Q1 seasonality"), a
    definition, or last year's note does not count as this month's reason."""
    tags = {str(t).strip().lower() for t in (doc.metric_tags or [])}
    if str(metric).strip().lower() not in tags:
        return False
    if doc.effective_date:
        start, until = str(doc.effective_date)[:7], getattr(doc, "effective_until", None)
        if until:   # covers a range, or recurs until turned off
            return start <= month and (until == "ongoing" or month <= str(until)[:7])
        return start == month
    return doc.type == "event_note"


def reason_note(docs, metric: str, month: str):
    """The newest note recording this KPI's reason for the month, or None."""
    found = [d for d in docs if reason_recorded(d, metric, month)]
    return max(found, key=lambda d: d.id) if found else None


def dataset_notes(conn, dataset_id):
    """This dataset's notes plus workspace-wide ones, without touching the vector index."""
    from app.context.store import _row_to_doc
    from app.datasets import _scope_pred

    rows = conn.execute(f"SELECT * FROM context_documents WHERE {_scope_pred()} ORDER BY id").fetchall()
    return [d for d in (_row_to_doc(r) for r in rows) if d.dataset_id is None or d.dataset_id == dataset_id]
