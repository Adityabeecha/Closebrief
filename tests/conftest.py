import pytest


@pytest.fixture(autouse=True)
def _auth_off_by_default(monkeypatch):
    """Tests are hermetic w.r.t. auth: default it OFF so a populated .env
    (SUPABASE_URL set for live dev) doesn't make TestClient calls 401.
    Auth-specific tests re-enable it per case via their own fixture."""
    from app.config import settings

    monkeypatch.setattr(settings, "supabase_url", "", raising=False)
    monkeypatch.setattr(settings, "supabase_jwt_secret", "", raising=False)
    # Same hermeticity for demo mode: a DEMO_MODE=true in .env would grant
    # anonymous reads and seed sample data, breaking auth/dataset tests.
    monkeypatch.setattr(settings, "demo_mode", False, raising=False)
