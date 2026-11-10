"""Session key separation warnings are production-only and emitted once."""

import logging

import pytest


@pytest.mark.parametrize("environment,secret,expected", [("prod", "", 1), ("prod", "dedicated-secret", 0), ("dev", "", 0)])
def test_session_secret_warning_once(monkeypatch, caplog, environment, secret, expected):
    from app import google_auth
    from app.config import settings

    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "session_jwt_secret", secret)
    monkeypatch.setattr(google_auth, "_session_secret_warned", False, raising=False)
    with caplog.at_level(logging.WARNING):
        google_auth.warn_session_secret_fallback()
        google_auth.warn_session_secret_fallback()
    warnings = [r for r in caplog.records if "SESSION_JWT_SECRET" in r.message]
    assert len(warnings) == expected
