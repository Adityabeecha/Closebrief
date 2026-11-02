"""Read-only month workspace summaries."""
import calendar
from datetime import datetime, timezone
import re

from fastapi import APIRouter, Depends, HTTPException

from app import audit, scheduling
from app.api import CurrentUser, require_read
from app.compute.availability import has_usable_value
from app.context.store import _row_to_doc
from app.datasets import _scope_pred, active_dataset_id, current_workspace
from app.db import get_connection
from app.retrieval.retrieve import _effective_ok, _metric_ok

router = APIRouter(prefix='/close', tags=['close'])


def _label(period):
    year, month = map(int, period.split('-'))
    return f'{calendar.month_name[month]} {year}'


def _summaries(conn, ds):
    """Progress: 20% imported, 20% checked share, 20% reason coverage,
    30% approved share, and 10% shared. Reason coverage counts only once
    something is drafted (an untouched close shows just its import); empty
    reason sets are then fully covered. Checked means the latest report
    passed the existing faithfulness check.
    """
    if ds is None:
        return []
    rows = conn.execute('''SELECT cf.period, cf.metric_id, cf.value, m.name, cf.is_anomaly,
        cf.budget_var_pct, gr.id AS report_id, gr.review_status, gr.faithfulness
        FROM computed_facts cf JOIN metrics m ON m.id = cf.metric_id
        LEFT JOIN generated_reports gr ON gr.id = (SELECT MAX(g.id) FROM generated_reports g
            WHERE g.metric_id = cf.metric_id AND g.period = cf.period
              AND (g.prompt_version IS NULL OR g.prompt_version NOT LIKE '%-digest'))
        WHERE m.dataset_id = ?
          AND (NOT EXISTS (SELECT 1 FROM kpi_configs k WHERE k.dataset_id=m.dataset_id)
               OR EXISTS (SELECT 1 FROM kpi_configs k WHERE k.dataset_id=m.dataset_id AND k.source_metric=m.name))
        ORDER BY cf.period DESC''', (ds,)).fetchall()
    docs = [_row_to_doc(r) for r in conn.execute(
        f'SELECT * FROM context_documents WHERE {_scope_pred()}').fetchall()]
    runs = scheduling.list_digest_runs(conn, ds, limit=2147483647)
    shared = {}
    for run in runs:
        timestamp = datetime.fromisoformat(run['created_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        shared.setdefault(run['period'], timestamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'))
    groups = {}
    for row in rows:
        if not has_usable_value(row['metric_id'], row['value']):
            continue
        period = row['period']
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) or period.startswith('0000'):
            continue
        group = groups.setdefault(period, {'kpis': 0, 'drafted': 0, 'approved': 0,
                                            'checked': 0, 'needs_reason': 0, 'exceptions': 0, 'flagged': 0})
        group['kpis'] += 1
        group['drafted'] += int(row['report_id'] is not None)
        group['approved'] += int(row['review_status'] == 'approved')
        group['checked'] += int(row['faithfulness'] == 'passed')
        group['exceptions'] += int(bool(row['is_anomaly']))
        flagged = bool(row['is_anomaly']) or (row['budget_var_pct'] is not None and abs(row['budget_var_pct']) > 10)
        if flagged:
            group['flagged'] += 1
            if not any(_metric_ok(doc, row['name']) and _effective_ok(doc, period) for doc in docs):
                group['needs_reason'] += 1
    out = []
    for period, g in groups.items():
        n = g['kpis']
        is_shared = period in shared
        coverage = (1 - g['needs_reason'] / g['flagged'] if g['flagged'] else 1) if g['drafted'] else 0
        progress = .2 + .2 * g['checked'] / n + .2 * coverage + .3 * g['approved'] / n + .1 * is_shared
        out.append({'period': period, 'label': _label(period),
                    'status': 'shared' if is_shared else 'in_progress' if g['drafted'] else 'not_started',
                    **g, 'progress': round(progress, 2), 'shared_at': shared.get(period)})
    return out


def _public(summary):
    return {k: v for k, v in summary.items() if k not in ('checked', 'flagged')}


@router.get('/months')
def months(_: CurrentUser = Depends(require_read)):
    conn = get_connection()
    try:
        return [_public(s) for s in _summaries(conn, active_dataset_id(conn))]
    finally:
        conn.close()


def _activity(conn, ds, period):
    reports = {str(r['id']): (r['period'], r['name']) for r in conn.execute(
        '''SELECT gr.id, gr.period, m.name FROM generated_reports gr JOIN metrics m ON m.id = gr.metric_id
           WHERE m.dataset_id = ?''', (ds,)).fetchall()}
    result = []
    for entry in audit.list_entries(conn, current_workspace(), limit=2147483647):
        summary = entry['summary']
        report = reports.get(str(entry['entity_id'])) if entry['entity_type'] == 'report' else None
        relevant = (report and report[0] == period) or (entry['entity_type'] == 'dataset' and str(entry['entity_id']) == str(ds))
        if not relevant:
            continue
        action = entry['action'].replace('_', ' ')
        text = f"{action.capitalize()} {report[1]} for {period}" if report else f"{action.capitalize()} dataset"
        result.append({'at': entry['created_at'], 'text': text, 'actor': entry['actor_email'] or entry['actor_id'] or 'System'})
        if len(result) == 10:
            break
    return result


@router.get('/status')
def status(period: str, _: CurrentUser = Depends(require_read)):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) or period.startswith('0000'):
        raise HTTPException(status_code=422, detail='Period must be YYYY-MM')
    conn = get_connection()
    try:
        ds = active_dataset_id(conn)
        s = next ( (x for x in _summaries(conn, ds) if x['period'] == period), None )
        if s is None:
            raise HTTPException(status_code=404, detail='No facts for period')
        remaining = s['kpis'] - s['approved']
        pending_drafts = s['drafted'] - s['approved']
        def n(count, one, many=None):
            return f"{count} {one if count == 1 else (many or one + 's')}"
        def step(id, title, detail, state, count, tab):
            return {'id': id, 'title': title, 'detail': detail, 'state': state, 'count': count,
                    'target': f'#month={period}&tab={tab}'}
        steps = [
            step('import', 'Import actuals and budget', f"{n(s['kpis'], 'figure')} imported", 'done', s['kpis'], 'data'),
            step('review', 'Review the biggest movers', f"{s['checked']} of {n(s['kpis'], 'figure')} checked", 'done' if s['checked'] == s['kpis'] else 'todo', s['checked'], 'kpis'),
            step('reasons', f"Add reasons for {n(s['needs_reason'], 'variance')}" if s['needs_reason'] else 'Every variance has a reason',
                 f"{n(s['needs_reason'], 'variance needs', 'variances need')} context" if s['needs_reason'] else 'No large variance is missing a context note', 'done' if not s['needs_reason'] else 'todo', s['needs_reason'], 'context'),
            step('approve', f"Approve {n(pending_drafts, 'draft')}" if pending_drafts else 'Approve the drafts', f"{s['approved']} of {s['kpis']} approved", 'done' if not remaining else 'todo' if pending_drafts else 'blocked', pending_drafts, 'kpis'),
            step('share', 'Send the digest', 'Digest sent' if s['status'] == 'shared' else 'Digest not sent', 'done' if s['status'] == 'shared' else 'todo' if not remaining else 'blocked', int(s['status'] == 'shared'), 'digest'),
        ]
        return {'period': period, 'label': s['label'], 'steps': steps, 'activity': _activity(conn, ds, period)}
    finally:
        conn.close()
