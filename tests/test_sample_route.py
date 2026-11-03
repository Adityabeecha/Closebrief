"""The sample export behind "Try it with sample data" is served without a session."""
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_sample_export_is_public_and_is_the_shipped_csv(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    r = TestClient(app).get("/samples/fpa.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0] == "period,metric,value,budget"
