"""Unit tests for the v2.1 notification channels & formatters (no network)."""

import hashlib
import hmac
import json

import pytest

SAMPLE = [
    {"metric": "Net Revenue", "period": "2025-03", "value": "$5.33M",
     "delta": "+27.6%", "narrative": "Pricing change lifted revenue."},
    {"metric": "COGS", "period": "2025-03", "value": "$2.03M", "delta": "+1.9%"},
]


def test_webhook_payload_format():
    from app.notifications.templates import webhook_payload
    p = webhook_payload("anomaly_detected", {"anomalies": SAMPLE})
    assert p["event"] == "anomaly_detected"
    assert p["source"] == "closebrief"
    assert "timestamp" in p and p["data"]["anomalies"] == SAMPLE
    # Serializable
    json.dumps(p)


def test_webhook_hmac_signature(monkeypatch):
    from app.config import settings
    from app.notifications.channels import WebhookChannel
    monkeypatch.setattr(settings, "webhook_secret", "s3cret")
    body = b'{"hello":"world"}'
    sig = WebhookChannel.sign(body)
    expected = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_email_template_rendering():
    from app.notifications.templates import render_digest_email
    subject, body = render_digest_email("2025-03", SAMPLE)
    assert "2025-03" in subject
    assert "Net Revenue" in body and "COGS" in body
    assert body.strip().startswith("<div") and "Pricing change" in body
    # HTML-escaping of a hostile value
    subject2, body2 = render_digest_email("2025-03", [{"metric": "<script>x</script>", "value": "1", "delta": "0"}])
    assert "<script>" not in body2 and "&lt;script&gt;" in body2


def test_slack_block_format():
    from app.notifications.templates import slack_anomaly_blocks
    payload = slack_anomaly_blocks(SAMPLE)
    assert payload["blocks"][0]["type"] == "header"
    assert any(b["type"] == "section" for b in payload["blocks"])
    assert "Net Revenue" in json.dumps(payload)


def test_make_channel_unknown_raises():
    from app.notifications.channels import NotificationError, make_channel
    with pytest.raises(NotificationError):
        make_channel("carrierpigeon", {})


def test_email_channel_requires_recipients(monkeypatch):
    from app.config import settings
    from app.notifications.channels import EmailChannel, NotificationError
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    with pytest.raises(NotificationError):
        EmailChannel({"recipients": []}).send_digest("2025-03", SAMPLE)


def test_deliver_filters_by_event(monkeypatch, tmp_path):
    """deliver() only calls channels subscribed to the event, and reports
    per-config failures without aborting the fan-out."""
    from app.config import settings
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "notif.db"))
    import app.db as db
    db.init_db()
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO notification_configs (channel, config, enabled) VALUES ('webhook', ?, 1)",
        (json.dumps({"url": "http://localhost:1/nope", "events": ["digest_generated"]}),),
    )
    conn.execute(
        "INSERT INTO notification_configs (channel, config, enabled) VALUES ('webhook', ?, 1)",
        (json.dumps({"url": "http://localhost:1/nope", "events": ["anomaly_detected"]}),),
    )
    conn.commit()

    from app.notifications import scheduler
    # Only the second config subscribes to anomaly_detected; its delivery will
    # fail (unroutable url) but must be reported, not raised.
    result = scheduler.deliver(conn, "anomaly_detected", items=SAMPLE)
    conn.close()
    assert result["sent"] == []
    assert len(result["failed"]) == 1 and result["failed"][0]["id"] == 2
