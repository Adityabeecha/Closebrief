"""Production must not start with auth on and no sign-in provider (it would serve data to anyone)."""
import pytest

from app.config import check_production_auth, settings


@pytest.mark.parametrize("env,auth,supabase,ok", [
    ("prod", True, "", False),                         # the dangerous case: refuse to start
    ("prod", True, "https://x.supabase.co", True),     # provider configured
    ("prod", False, "", True),                         # explicitly open, on purpose
    ("dev", True, "", True),                           # local dev keeps the bypass
])
def test_production_auth_fails_closed(monkeypatch, env, auth, supabase, ok):
    monkeypatch.setattr(settings, "environment", env)
    monkeypatch.setattr(settings, "auth_enabled", auth)
    monkeypatch.setattr(settings, "supabase_url", supabase)
    monkeypatch.setattr(settings, "google_client_id", "")
    if ok:
        check_production_auth()
    else:
        with pytest.raises(RuntimeError, match="no sign-in provider"):
            check_production_auth()
