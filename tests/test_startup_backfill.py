"""Startup must not wait for the one-time anomaly recompute: on a remote database it
takes minutes, and Render fails a deploy whose server doesn't bind its port."""

import threading
import time

from fastapi.testclient import TestClient


def test_startup_does_not_wait_for_the_anomaly_backfill(monkeypatch):
    import app.compute.kpis as kpis
    from app.main import app

    started, release = threading.Event(), threading.Event()

    def slow(conn):
        started.set()
        release.wait(10)
        return 0

    monkeypatch.setattr(kpis, "refresh_stale_anomalies", slow)
    t0 = time.monotonic()
    try:
        with TestClient(app) as client:
            assert time.monotonic() - t0 < 5
            assert client.get("/health").status_code == 200
            assert started.wait(5)          # it did start, off the startup path
    finally:
        release.set()
