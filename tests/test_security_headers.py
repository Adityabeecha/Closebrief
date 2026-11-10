"""S7/S11: browser protections on every response; no public API docs in production."""

import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_every_response_carries_the_security_headers():
    from app.main import app
    c = TestClient(app)
    for path in ("/", "/app", "/health", "/app.js"):
        h = c.get(path).headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "SAMEORIGIN"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        csp = h["content-security-policy"]
        for part in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'self'", "base-uri 'self'"):
            assert part in csp, (path, part)


def test_policy_allows_configured_sign_in_and_error_tracking(monkeypatch):
    from app import main
    from app.config import settings
    monkeypatch.setattr(settings, "supabase_url", "https://abc.supabase.co/")
    monkeypatch.setattr(settings, "sentry_dsn", "https://k@o1.ingest.us.sentry.io/2")
    monkeypatch.setattr(settings, "google_client_id", "id.apps.googleusercontent.com")
    csp = main._content_security_policy()
    connect = next(p for p in csp.split("; ") if p.startswith("connect-src"))
    assert "https://abc.supabase.co" in connect and "https://*.sentry.io" in connect
    assert "https://accounts.google.com" in connect


def test_production_does_not_publish_api_docs():
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "APP_ENV": "prod", "ENVIRONMENT": "prod", "SENTRY_DSN": ""}
    out = subprocess.run(
        [sys.executable, "-c", "import app.main as m; print(m.app.docs_url, m.app.redoc_url, m.app.openapi_url)"],
        cwd=root, env=env, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "None None None"
