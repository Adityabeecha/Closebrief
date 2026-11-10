"""Sandbox expiry uses all activity sources and preserves admin/demo data."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from dbharness import use_test_db

OLD = "2020-01-01 00:00:00"


@pytest.fixture
def conn(monkeypatch, tmp_path):
    from app import db, deps
    from app.config import settings

    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "retention.db"))
    monkeypatch.setattr(deps, "shared_vector_store", lambda: Mock())
    db.init_db()
    connection = db.get_connection()
    try:
        yield connection
    finally:
        connection.close()


def _sandbox(conn, *, admin=False, age=OLD):
    from app import datasets, workspaces

    user_id = str(uuid4())
    ws = workspaces.create_workspace(conn, "Visitor", user_id)
    conn.execute("UPDATE workspaces SET created_at = ? WHERE id = ?", (age, ws))
    if admin:
        conn.execute("INSERT INTO app_roles (user_id, email, role) VALUES (?, ?, 'admin')", (user_id, "owner@example.com"))
    ds = datasets.create_dataset(conn, "Private data", workspace_id=ws, activate=False)
    conn.execute("UPDATE datasets SET created_at = ? WHERE id = ?", (age, ds))
    metric = datasets.get_or_create_metric(conn, ds, "Revenue")
    report = conn.execute(
        "INSERT INTO generated_reports (metric_id, period, confidence, faithfulness, created_at) "
        "VALUES (?, '2020-01', 'high', 'pass', ?) RETURNING id", (metric, age),
    ).fetchone()["id"]
    conn.execute("INSERT INTO metric_values (metric_id, period, value) VALUES (?, '2020-01', 100)", (metric,))
    conn.execute("INSERT INTO computed_facts (metric_id, period, value) VALUES (?, '2020-01', 100)", (metric,))
    conn.execute("INSERT INTO feedback (report_id, action) VALUES (?, 'accept')", (report,))
    conn.execute("INSERT INTO report_versions (report_id, version, narrative) VALUES (?, 1, 'old private narrative')", (report,))
    conn.execute("INSERT INTO derived_metrics (dataset_id, name, formula) VALUES (?, 'Derived', 'Revenue * 2')", (ds,))
    conn.execute("INSERT INTO context_documents (type, title, body, workspace_id, created_at) VALUES ('note', 'Private', 'secret', ?, ?)",
                 (ws, age))
    conn.execute("INSERT INTO connectors (workspace_id, kind, name) VALUES (?, 'csv_url', 'Private connector')", (ws,))
    conn.execute("INSERT INTO notification_configs (workspace_id, channel, config) VALUES (?, 'webhook', '{}')", (ws,))
    conn.execute("INSERT INTO llm_calls (endpoint, workspace_id, created_at) VALUES ('/x', ?, ?)", (ws, age))
    conn.execute("INSERT INTO uploads (id, filename, workspace_id) VALUES (?, 'private.csv', ?)", (str(uuid4()), ws))
    job = conn.execute("INSERT INTO scheduled_jobs (kind, cadence, dataset_id) VALUES ('digest', 'daily', ?) RETURNING id",
                       (ds,)).fetchone()["id"]
    conn.execute("INSERT INTO scheduler_runs (job_id, kind, status) VALUES (?, 'digest', 'ok')", (job,))
    conn.execute("INSERT INTO digest_runs (dataset_id, period, top_n, items) VALUES (?, '2020-01', 1, '[]')", (ds,))
    workspaces.create_invite(conn, ws)
    conn.commit()
    return ws, ds, user_id


def test_old_visitor_and_all_owned_data_are_removed(conn):
    from app import datasets, retention

    ws, _, _ = _sandbox(conn)
    demo = datasets.create_dataset(conn, "Demo", is_demo=True, activate=False)
    conn.execute("UPDATE datasets SET created_at = ? WHERE id = ?", (OLD, demo))
    conn.execute("INSERT INTO context_documents (type, title, body, is_demo) VALUES ('note', 'Demo', 'public', true)")
    conn.commit()
    ws_token = datasets._workspace_scope.set(999)
    demo_token = datasets._demo_scope.set(True)
    try:
        result = retention.purge_inactive_sandboxes(conn)
        assert result == {"workspaces_purged": 1, "datasets_purged": 1}
        assert datasets.current_workspace() == 999 and datasets.is_demo_scope()
    finally:
        datasets._workspace_scope.reset(ws_token)
        datasets._demo_scope.reset(demo_token)
    assert conn.execute("SELECT id FROM workspaces WHERE id = ?", (ws,)).fetchone() is None
    for table in ("workspace_members", "workspace_invites", "connectors", "notification_configs", "llm_calls", "uploads"):
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE workspace_id = ?", (ws,)).fetchone()["n"] == 0
    for table in ("metrics", "metric_values", "computed_facts", "generated_reports", "feedback", "report_versions",
                  "derived_metrics", "scheduled_jobs", "scheduler_runs", "digest_runs"):
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
    assert conn.execute("SELECT id FROM datasets WHERE id = ?", (demo,)).fetchone() is not None
    docs = conn.execute("SELECT title FROM context_documents").fetchall()
    assert [r["title"] for r in docs] == ["Demo"]


@pytest.mark.parametrize("source", ["datasets", "generated_reports", "context_documents", "llm_calls"])
def test_each_recent_activity_source_keeps_workspace(conn, source):
    from app import retention

    ws, ds, _ = _sandbox(conn)
    if source == "datasets":
        conn.execute("UPDATE datasets SET created_at = CURRENT_TIMESTAMP WHERE id = ?", (ds,))
    elif source == "generated_reports":
        conn.execute("UPDATE generated_reports SET created_at = CURRENT_TIMESTAMP")
    else:
        conn.execute(f"UPDATE {source} SET created_at = CURRENT_TIMESTAMP WHERE workspace_id = ?", (ws,))
    conn.commit()
    assert retention.purge_inactive_sandboxes(conn)["workspaces_purged"] == 0
    assert conn.execute("SELECT id FROM workspaces WHERE id = ?", (ws,)).fetchone() is not None


@pytest.mark.parametrize("protection", ["admin", "keep-policy", "keep-user", "demo"])
def test_protected_workspace_survives_regardless_of_age(conn, protection):
    from app import retention

    ws, ds, user = _sandbox(conn, admin=protection == "admin")
    if protection == "keep-policy":
        conn.execute("UPDATE workspaces SET retention_days = 0 WHERE id = ?", (ws,))
    if protection == "demo":
        conn.execute("UPDATE datasets SET is_demo = true WHERE id = ?", (ds,))
    conn.commit()
    keep = (user,) if protection == "keep-user" else ()
    assert retention.purge_inactive_sandboxes(conn, keep_user_ids=keep)["workspaces_purged"] == 0
    assert conn.execute("SELECT id FROM workspaces WHERE id = ?", (ws,)).fetchone() is not None


def test_empty_workspace_uses_creation_time(conn):
    from app import retention, workspaces

    old = workspaces.create_workspace(conn, "Empty old workspace", str(uuid4()))
    recent = workspaces.create_workspace(conn, "Empty recent workspace", str(uuid4()))
    conn.execute("UPDATE workspaces SET created_at = ? WHERE id = ?", (OLD, old))
    conn.commit()
    assert retention.purge_inactive_sandboxes(conn)["workspaces_purged"] == 1
    assert conn.execute("SELECT id FROM workspaces WHERE id = ?", (recent,)).fetchone() is not None


def test_scheduled_retention_respects_disable_and_runs_before_telemetry_trim(conn, monkeypatch):
    from app import retention
    from app.config import settings

    ws, _, _ = _sandbox(conn)
    monkeypatch.setattr(settings, "sandbox_retention_days", 0)
    retention.run_retention(conn)
    assert conn.execute("SELECT id FROM workspaces WHERE id = ?", (ws,)).fetchone() is not None
    monkeypatch.setattr(settings, "sandbox_retention_days", 30)
    assert retention.run_retention(conn)["sandboxes_purged"] == 1
    assert conn.execute("SELECT id FROM workspaces WHERE id = ?", (ws,)).fetchone() is None


def test_recent_activity_is_checked_before_workspace_telemetry_is_trimmed(conn):
    from app import retention

    ws, _, _ = _sandbox(conn)
    conn.execute("UPDATE workspaces SET retention_days = 1 WHERE id = ?", (ws,))
    conn.execute("UPDATE llm_calls SET created_at = ? WHERE workspace_id = ?", (retention._cutoff(5), ws))
    conn.commit()
    retention.run_retention(conn)
    assert conn.execute("SELECT id FROM workspaces WHERE id = ?", (ws,)).fetchone() is not None


def test_scheduler_does_not_run_jobs_deleted_by_retention_earlier_in_tick(conn):
    from app import scheduling

    retention_job = scheduling.create_job(conn, "retention_purge", "daily")
    _sandbox(conn)
    result = scheduling.run_due_jobs(conn, llm_client=None)
    assert [job["id"] for job in result["ran"]] == [retention_job]
    logs = conn.execute("SELECT job_id FROM scheduler_runs").fetchall()
    assert [row["job_id"] for row in logs] == [retention_job]


def test_dataset_bound_retention_job_does_not_recreate_its_deleted_history(conn):
    from app import scheduling

    ws, ds, _ = _sandbox(conn)
    conn.execute("DELETE FROM scheduled_jobs WHERE dataset_id = ?", (ds,))
    conn.execute("DELETE FROM scheduler_runs")
    conn.commit()
    retention_job = scheduling.create_job(conn, "retention_purge", "daily", dataset_id=ds)
    result = scheduling.run_due_jobs(conn, llm_client=None)
    assert result["ran"][0]["id"] == retention_job
    assert result["ran"][0]["sandboxes_purged"] == 1
    assert conn.execute("SELECT id FROM workspaces WHERE id = ?", (ws,)).fetchone() is None
    assert conn.execute("SELECT COUNT(*) AS n FROM scheduler_runs").fetchone()["n"] == 0


def test_faiss_cleanup_also_runs_with_postgres_storage(conn, monkeypatch):
    from app import deps, retention
    from app.config import settings

    class PostgresStorage:
        backend = "postgres"

        def __getattr__(self, name):
            return getattr(conn, name)

    _sandbox(conn)
    documents = conn.execute("SELECT id FROM context_documents").fetchall()
    vectors = Mock()
    monkeypatch.setattr(settings, "vector_backend", "faiss")
    monkeypatch.setattr(deps, "shared_vector_store", lambda: vectors)
    retention.purge_inactive_sandboxes(PostgresStorage())
    vectors.remove.assert_called_once_with([int(row["id"]) for row in documents])
    vectors.persist.assert_called_once()


@pytest.mark.parametrize("kind", ["retention_purge", "connector_sync"])
@pytest.mark.parametrize("bound_dataset", [False, True])
def test_global_job_failure_never_uses_a_visitors_active_dataset(conn, monkeypatch, kind, bound_dataset):
    from app import datasets, scheduling
    from app.notifications import scheduler

    ws, ds, _ = _sandbox(conn)
    delivered = Mock()
    monkeypatch.setattr(scheduler, "deliver", delivered)
    ws_token = datasets._workspace_scope.set(ws)
    demo_token = datasets._demo_scope.set(False)
    try:
        scheduling._alert_job_failure(conn, {"id": 1, "kind": kind, "dataset_id": ds if bound_dataset else None}, "failure")
        assert delivered.call_args.kwargs["workspace_id"] is None
    finally:
        datasets._workspace_scope.reset(ws_token)
        datasets._demo_scope.reset(demo_token)


def test_dataset_job_failure_uses_the_implicit_active_dataset_workspace(conn, monkeypatch):
    from app import datasets, scheduling
    from app.notifications import scheduler

    ws, _, _ = _sandbox(conn)
    delivered = Mock()
    monkeypatch.setattr(scheduler, "deliver", delivered)
    ws_token = datasets._workspace_scope.set(ws)
    demo_token = datasets._demo_scope.set(False)
    try:
        scheduling._alert_job_failure(conn, {"id": 1, "kind": "digest", "dataset_id": None}, "failure")
        assert delivered.call_args.kwargs["workspace_id"] == ws
    finally:
        datasets._workspace_scope.reset(ws_token)
        datasets._demo_scope.reset(demo_token)
