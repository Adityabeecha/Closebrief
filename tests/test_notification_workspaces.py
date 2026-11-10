"""Workspace notification isolation; all outbound delivery is stubbed."""

import json
from unittest.mock import Mock

import pytest
from dbharness import use_test_db
from fastapi.testclient import TestClient


@pytest.fixture
def scoped_notifications(monkeypatch, tmp_path):
    from app import db, main
    from app.config import settings
    from app.routers import notifications

    use_test_db(monkeypatch)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "notifications.db"))
    db.init_db()
    scope = {"workspace": 101}
    monkeypatch.setattr(notifications, "current_workspace", lambda: scope["workspace"], raising=False)
    channel = Mock()
    monkeypatch.setattr(notifications, "make_channel", lambda *args: channel)
    return TestClient(main.app), scope, channel


def test_configs_are_private_and_mutations_are_scoped(scoped_notifications):
    client, scope, channel = scoped_notifications
    response = client.post("/notifications/configs", json={"channel": "webhook", "config": {"url": "https://a.invalid"}})
    assert response.status_code == 201
    config_id = response.json()["id"]
    assert len(client.get("/notifications/configs").json()) == 1
    scope["workspace"] = 202
    assert client.get("/notifications/configs").json() == []
    assert client.put(f"/notifications/{config_id}", json={"enabled": False}).status_code == 404
    assert client.post(f"/notifications/test/{config_id}").status_code == 404
    client.delete(f"/notifications/{config_id}")
    channel.send_anomaly_alert.assert_not_called()
    scope["workspace"] = 101
    assert client.get("/notifications/configs").json()[0]["enabled"] is True
    assert client.post(f"/notifications/test/{config_id}").status_code == 200
    channel.send_anomaly_alert.assert_called_once()
    assert client.delete(f"/notifications/{config_id}").status_code == 204
    assert client.get("/notifications/configs").json() == []


@pytest.mark.parametrize("workspace", [101, 202, None])
def test_anomaly_delivery_only_reaches_its_workspace(scoped_notifications, monkeypatch, workspace):
    from app.db import get_connection
    from app.notifications import scheduler

    channel = Mock()
    monkeypatch.setattr(scheduler, "make_channel", lambda *args: channel)
    conn = get_connection()
    try:
        for ws in (101, 202, None):
            conn.execute(
                "INSERT INTO notification_configs (channel, config, enabled, workspace_id) VALUES (?, ?, ?, ?)",
                ("webhook", json.dumps({"url": "https://example.invalid"}), True, ws),
            )
        conn.commit()
        expected = conn.execute("SELECT id FROM notification_configs WHERE workspace_id IS ?", (workspace,)).fetchone()["id"]
        assert scheduler.deliver(conn, "anomaly_detected", workspace_id=workspace, items=[{"metric": "Revenue"}]) == {
            "sent": [expected], "failed": [],
        }
        channel.send_anomaly_alert.assert_called_once()
    finally:
        conn.close()


def test_async_delivery_captures_workspace_before_pool_submission(scoped_notifications, monkeypatch):
    from app import datasets, main

    queued = []
    delivered = Mock()
    monkeypatch.setattr(main._bg_executor, "submit", queued.append)
    monkeypatch.setattr(main, "notif_deliver", delivered)
    token = datasets._workspace_scope.set(101)
    try:
        main._notify_async("anomaly_detected")
        datasets.set_workspace_scope(202)
        queued[0]()
        assert delivered.call_args.kwargs["workspace_id"] == 101
    finally:
        datasets._workspace_scope.reset(token)


def test_scheduled_failure_alert_uses_dataset_workspace(scoped_notifications, monkeypatch):
    from app import datasets, scheduling
    from app.db import get_connection
    from app.notifications import scheduler

    conn = get_connection()
    try:
        ds = datasets.create_dataset(conn, "Visitor data", workspace_id=202)
        delivered = Mock(return_value={"sent": [], "failed": []})
        monkeypatch.setattr(scheduler, "deliver", delivered)
        scheduling._alert_job_failure(conn, {"id": 1, "kind": "digest", "dataset_id": ds}, "test failure")
        assert delivered.call_args.kwargs["workspace_id"] == 202
    finally:
        conn.close()
