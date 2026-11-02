import pytest
from test_auth import make_client, _bearer


@pytest.fixture
def seeded(make_client):
    client = make_client(auth_on=False)
    from app.db import get_connection
    from app.datasets import create_dataset
    from app.scheduling import record_digest_run
    conn = get_connection()
    ds = create_dataset(conn, 'Close test')
    ids = []
    for name in ('Revenue', 'Cost', 'Cash'):
        cur = conn.execute('INSERT INTO metrics (dataset_id, name) VALUES (?, ?)', (ds, name))
        ids.append(cur.lastrowid)
    for period in ('2026-05', '2026-06'):
        for i, mid in enumerate(ids):
            conn.execute('INSERT INTO computed_facts (metric_id, period, value, is_anomaly, budget_var_pct) VALUES (?, ?, ?, ?, ?)', (mid, period, 100, int(i == 0), 0))
    for mid, status in ((ids[0], 'approved'), (ids[1], 'pending')):
        conn.execute("INSERT INTO generated_reports (metric_id, period, confidence, faithfulness, review_status) VALUES (?, '2026-06', 'High', 'passed', ?)", (mid, status))
    conn.execute("INSERT INTO generated_reports (metric_id, period, confidence, faithfulness, review_status) VALUES (?, '2026-06', 'High', 'passed', 'pending')", (ids[0],))
    conn.commit()
    record_digest_run(conn, ds, '2026-05', 5, [], 0, 'manual')
    conn.close()
    return client


def test_month_order_counts_and_latest_approval(seeded):
    response = seeded.get('/close/months')
    assert response.status_code == 200
    june, may = response.json()
    assert [june['period'], may['period']] == ['2026-06', '2026-05']
    assert (june['kpis'], june['drafted'], june['approved'], june['needs_reason'], june['exceptions']) == (3, 2, 0, 1, 1)
    assert june['status'] == 'in_progress'
    assert may['status'] == 'shared' and may['shared_at']


def test_status_steps_and_validation(seeded):
    data = seeded.get('/close/status?period=2026-06').json()
    assert data['label'] == 'June 2026'
    assert [s['id'] for s in data['steps']] == ['import', 'review', 'reasons', 'approve', 'share']
    assert all(s['target'].startswith('#month=2026-06&tab=') for s in data['steps'])
    assert data['steps'][0]['state'] == 'done'
    assert data['steps'][2]['count'] == 1
    assert data['steps'][4]['state'] != 'done'
    assert seeded.get('/close/status?period=2026-13').status_code == 422
    assert seeded.get('/close/status?period=bad').status_code == 422
    assert seeded.get('/close/status?period=2024-01').status_code == 404


@pytest.mark.sqlite_only
def test_viewer_can_read(make_client):
    c = make_client(auth_on=True, seed_roles={'v1': ('v@co.com', 'viewer')})
    assert c.get('/close/months', headers=_bearer(sub='v1', email='v@co.com')).status_code == 200
    assert c.get('/close/status?period=2026-06', headers=_bearer(sub='v1', email='v@co.com')).status_code == 404


def test_variance_threshold_context_and_progress(seeded):
    from app.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE computed_facts SET budget_var_pct = 10 WHERE period = '2026-06' AND metric_id = (SELECT id FROM metrics WHERE name = 'Cost')")
    conn.execute("UPDATE computed_facts SET budget_var_pct = -10.1 WHERE period = '2026-06' AND metric_id = (SELECT id FROM metrics WHERE name = 'Cash')")
    conn.execute("INSERT INTO context_documents (type, title, body, metric_tags, effective_date) VALUES ('event_note', 'Revenue reason', 'Reason', '[\"Revenue\"]', '2026-06')")
    conn.commit()
    conn.close()
    june = seeded.get('/close/months').json()[0]
    assert june['needs_reason'] == 1  # Cash exceeds 10%; Revenue is covered.
    assert june['exceptions'] == 1
    assert june['progress'] == 0.43


def test_activity_scoped_and_shared_step(seeded):
    from app.db import get_connection
    from app import audit
    conn = get_connection()
    report = conn.execute("SELECT gr.id FROM generated_reports gr JOIN metrics m ON m.id=gr.metric_id WHERE m.name='Revenue' AND gr.period='2026-06' ORDER BY gr.id DESC LIMIT 1").fetchone()['id']
    audit.record(conn, 'approved', 'report', report, actor_email='reviewer@example.com')
    audit.record(conn, 'login', 'user', 'somebody', actor_email='other@example.com')
    conn.close()
    data = seeded.get('/close/status?period=2026-06').json()
    assert data['activity'] == [{'at': data['activity'][0]['at'], 'text': 'Approved Revenue for 2026-06', 'actor': 'reviewer@example.com'}]
    may = seeded.get('/close/status?period=2026-05').json()
    assert may['steps'][-1]['state'] == 'done'



@pytest.mark.sqlite_only
def test_close_counts_use_only_selected_kpis_with_usable_values(seeded):
    from app.db import get_connection
    conn = get_connection()
    ds = conn.execute("SELECT id FROM datasets WHERE name='Close test'").fetchone()['id']
    # Simulate a legacy nullable fact row. Current ingestion rejects NULL, but
    # /facts explicitly supports it and close counts must follow that contract.
    conn.execute("CREATE TABLE nullable_facts AS SELECT * FROM computed_facts")
    conn.execute("DROP TABLE computed_facts")
    conn.execute("ALTER TABLE nullable_facts RENAME TO computed_facts")
    mid = conn.execute("INSERT INTO metrics (dataset_id,name) VALUES (?, 'Empty')", (ds,)).lastrowid
    conn.execute("INSERT INTO computed_facts (metric_id,period,value,is_anomaly,budget_var_pct) VALUES (?, '2026-06', NULL, 1, -90)", (mid,))
    conn.execute("INSERT INTO generated_reports (metric_id,period,confidence,faithfulness,review_status) VALUES (?, '2026-06', 'High','passed','approved')", (mid,))
    # Match /facts' curated set: an unselected Cash row must not inflate counts.
    for name in ('Revenue', 'Cost', 'Empty'):
        conn.execute("INSERT INTO kpi_configs (dataset_id,source_metric,display_name) VALUES (?,?,?)", (ds,name,name))
    conn.commit()
    conn.close()
    facts = seeded.get('/facts?period=2026-06').json()
    summary = seeded.get('/close/months').json()[0]
    assert summary['kpis'] == sum(f['has_data'] for f in facts) == 2
    assert (summary['drafted'],summary['approved'],summary['needs_reason'],summary['exceptions']) == (2,0,1,1)
    steps = seeded.get('/close/status?period=2026-06').json()['steps']
    assert steps[0]['count'] == 2
    assert steps[1]['state'] == 'done'  # both usable KPIs passed the check
