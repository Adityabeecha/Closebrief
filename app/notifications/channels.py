"""Notification channels. Each takes a per-config dict and knows how to deliver
a digest or an anomaly alert. Network I/O uses the stdlib (smtplib / urllib) so
there is no extra runtime dependency.

A channel's `config` dict shape:
  email:   {"recipients": ["a@x.com", ...], "events": [...], "schedule": "..."}
  slack:   {"webhook_url": "https://hooks.slack.com/...", "events": [...]}
  webhook: {"url": "https://...", "events": [...]}   (secret from settings)
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import smtplib
import socket
import urllib.error
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

from app.config import settings
from app.notifications import templates


class NotificationError(RuntimeError):
    pass


def validate_public_url(url: str) -> str:
    """Reject anything that isn't a plain http(s) URL resolving to a public IP —
    blocks SSRF to loopback, private ranges, and the cloud metadata endpoint
    (169.254.169.254). Operator-configured webhook/Slack URLs pass through here.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise NotificationError(f"Invalid URL: {url!r}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise NotificationError("Webhook URL must be an http(s) URL")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise NotificationError(f"Could not resolve host {parsed.hostname!r}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise NotificationError(
                f"Webhook host {parsed.hostname!r} resolves to a non-public address — blocked"
            )
    return url


class NotificationChannel:
    channel = "base"

    def __init__(self, config: dict):
        self.config = config or {}

    def send_digest(self, period: str, items: list[dict]) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def send_anomaly_alert(self, anomalies: list[dict]) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


def _http_post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 10) -> int:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - url is operator-configured
        return resp.status


class EmailChannel(NotificationChannel):
    channel = "email"

    def _send(self, subject: str, html_body: str) -> None:
        recipients = self.config.get("recipients") or []
        if not recipients:
            raise NotificationError("No recipients configured")
        # Prefer Resend's HTTP API when configured — works on hosts that block
        # outbound SMTP (Render, many PaaS). Falls back to raw SMTP otherwise.
        if settings.resend_api_key:
            self._send_resend(subject, html_body, recipients)
            return
        if not settings.smtp_host:
            raise NotificationError(
                "Email is not configured. Set RESEND_API_KEY (recommended) or SMTP_HOST."
            )
        self._send_smtp(subject, html_body, recipients)

    def _send_resend(self, subject: str, html_body: str, recipients: list[str]) -> None:
        payload = {
            "from": settings.smtp_from,
            "to": recipients,
            "subject": subject,
            "html": html_body,
        }
        try:
            _http_post_json(
                "https://api.resend.com/emails", payload,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"}, timeout=15,
            )
        except urllib.error.HTTPError as e:  # surface Resend's human-readable message
            raw = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(raw).get("message", raw)
            except ValueError:
                msg = raw
            raise NotificationError(f"Resend rejected the email ({e.code}): {msg[:400]}") from e

    def _send_smtp(self, subject: str, html_body: str, recipients: list[str]) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html"))
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=12) as s:
                if settings.smtp_starttls:
                    s.starttls()
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_pass)
                s.sendmail(settings.smtp_from, recipients, msg.as_string())
        except (OSError, smtplib.SMTPException) as e:
            raise NotificationError(
                f"Could not reach SMTP server {settings.smtp_host}:{settings.smtp_port} "
                f"({e}). Outbound SMTP may be blocked — consider RESEND_API_KEY instead."
            ) from e

    def send_digest(self, period: str, items: list[dict]) -> None:
        self._send(*templates.render_digest_email(period, items))

    def send_anomaly_alert(self, anomalies: list[dict]) -> None:
        self._send(*templates.render_anomaly_email(anomalies))


class SlackChannel(NotificationChannel):
    channel = "slack"

    def _url(self) -> str:
        url = self.config.get("webhook_url")
        if not url:
            raise NotificationError("No Slack webhook_url configured")
        return validate_public_url(url)

    def send_digest(self, period: str, items: list[dict]) -> None:
        _http_post_json(self._url(), templates.slack_digest_blocks(period, items))

    def send_anomaly_alert(self, anomalies: list[dict]) -> None:
        _http_post_json(self._url(), templates.slack_anomaly_blocks(anomalies))


class WebhookChannel(NotificationChannel):
    channel = "webhook"

    def _url(self) -> str:
        url = self.config.get("url")
        if not url:
            raise NotificationError("No webhook url configured")
        return validate_public_url(url)

    @staticmethod
    def sign(body: bytes) -> str:
        """HMAC-SHA256 hex signature of the raw JSON body, using WEBHOOK_SECRET."""
        secret = (settings.webhook_secret or "").encode("utf-8")
        return hmac.new(secret, body, hashlib.sha256).hexdigest()

    def _post(self, event: str, data: dict) -> None:
        payload = templates.webhook_payload(event, data)
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if settings.webhook_secret:
            headers["X-Closebrief-Signature"] = "sha256=" + self.sign(body)
        req = urllib.request.Request(self._url(), data=body, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=10):  # noqa: S310 - operator-configured url
            pass

    def send_digest(self, period: str, items: list[dict]) -> None:
        self._post("digest_generated", {"period": period, "items": items})

    def send_anomaly_alert(self, anomalies: list[dict]) -> None:
        self._post("anomaly_detected", {"anomalies": anomalies})


_CHANNELS = {c.channel: c for c in (EmailChannel, SlackChannel, WebhookChannel)}


def make_channel(channel: str, config: dict) -> NotificationChannel:
    cls = _CHANNELS.get(channel)
    if cls is None:
        raise NotificationError(f"Unknown channel: {channel}")
    return cls(config)
