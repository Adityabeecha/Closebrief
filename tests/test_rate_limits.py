"""Per-visitor rate limiting through trusted proxies (no auth/network calls)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app import main
    from app.config import settings

    monkeypatch.setattr(main, "_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "trust_proxy_headers", False, raising=False)
    return TestClient(main.app)


def test_forwarded_visitors_are_keyed_by_the_hop_the_proxy_added(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "trust_proxy_headers", True, raising=False)
    # The proxy appends the address it saw; whatever the visitor wrote before it is ignored.
    assert client.get("/auth/config", headers={"X-Forwarded-For": "spoof-a, 198.51.100.1"}).status_code == 200
    assert client.get("/auth/config", headers={"X-Forwarded-For": "spoof-b, 198.51.100.1"}).status_code == 429
    assert client.get("/auth/config", headers={"X-Forwarded-For": "spoof-a, 198.51.100.2"}).status_code == 200


def test_cloudflare_visitor_header_wins_when_trusted(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "trust_proxy_headers", True, raising=False)
    h = {"CF-Connecting-IP": "198.51.100.9"}
    assert client.get("/auth/config", headers={**h, "X-Forwarded-For": "198.51.100.9, 10.0.0.1"}).status_code == 200
    assert client.get("/auth/config", headers={**h, "X-Forwarded-For": "198.51.100.9, 10.0.0.2"}).status_code == 429


def test_untrusted_forwarded_headers_are_ignored(client):
    assert client.get("/auth/config", headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 200
    assert client.get("/auth/config", headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 429


def test_bearer_tokens_have_separate_buckets_and_are_hashed(client, monkeypatch):
    import hashlib

    from app import main
    from app.config import settings

    monkeypatch.setattr(settings, "trust_proxy_headers", True, raising=False)
    assert client.get("/auth/config").status_code == 200
    assert client.get("/auth/config", headers={"Authorization": "Bearer visitor-a"}).status_code == 200
    assert client.get("/auth/config", headers={"Authorization": "Bearer visitor-b"}).status_code == 200
    assert client.get("/auth/config", headers={"Authorization": "Bearer visitor-a", "X-Forwarded-For": "203.0.113.3"}).status_code == 429
    assert "token:" + hashlib.sha256(b"Bearer visitor-a").hexdigest() in main._RATE_BUCKETS
    assert not any("visitor-a" in key for key in main._RATE_BUCKETS)


def test_empty_forwarded_address_falls_back_to_peer(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "trust_proxy_headers", True, raising=False)
    assert client.get("/auth/config", headers={"X-Forwarded-For": " , "}).status_code == 200
    assert client.get("/auth/config").status_code == 429
