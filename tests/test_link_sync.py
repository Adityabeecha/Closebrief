"""Link sync reads a file like an upload, turns Drive share links into downloads, and
explains failures in words the user can act on."""

import pytest
from test_datasets import client as client

from app import connectors
from app.db import get_connection

DEPT = (b"period,metric,department,actual,budget,forecast\n"
        b"2025-01,Opex,Sales,110,100,105\n2025-01,Opex,R&D,90,100,95\n"
        b"2025-02,Opex,Sales,120,100,105\n2025-02,Opex,R&D,95,100,95\n")


@pytest.mark.parametrize("url,expected", [
    ("https://drive.google.com/file/d/1s8t2KrjXmPw99-UN3Yxu23oV7MlchUzg/view?usp=sharing",
     "https://drive.google.com/uc?export=download&id=1s8t2KrjXmPw99-UN3Yxu23oV7MlchUzg"),
    ("https://drive.google.com/open?id=1CLvcTMLUltbyR4r1UElAQLS9idH0ODYf",
     "https://drive.google.com/uc?export=download&id=1CLvcTMLUltbyR4r1UElAQLS9idH0ODYf"),
    ("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit#gid=42",
     "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/export?format=csv&gid=42"),
    ("https://example.com/actuals.csv", "https://example.com/actuals.csv"),
])
def test_share_links_become_download_links(url, expected):
    assert connectors.source_url(url) == expected


def _sync(raw):
    conn = get_connection()
    try:
        cid = connectors.create_connector(conn, "csv_url", "Link", {"url": "https://example.com/f.csv"}, "Linked", None)
        return conn, cid, connectors.sync_connector(conn, cid, None, fetch=lambda _: raw)
    except Exception:
        conn.close()
        raise


def test_sync_maps_columns_like_an_upload(client):
    conn, cid, result = _sync(DEPT)
    try:
        assert result["status"] == "ok", result
        assert result["mapping"]["value_col"] == "actual" and result["mapping"]["dimension_cols"] == ["department"]
        assert result["mapping"]["forecast_col"] == "forecast"
        value = conn.execute("""SELECT mv.value, mv.forecast FROM metric_values mv JOIN metrics m ON m.id=mv.metric_id
                                WHERE m.name='Opex' AND mv.period='2025-01'""").fetchone()
        assert (value["value"], value["forecast"]) == (200, 200)
        saved = conn.execute("SELECT config FROM connectors WHERE id = ?", (cid,)).fetchone()["config"]
        assert '"mapping"' in saved      # later syncs read it the same way
    finally:
        conn.close()


@pytest.mark.parametrize("raw,phrase", [
    (b"<!DOCTYPE html><html><body>Google Drive</body></html>", "returned a web page"),
    (b"period,metric,value\n2025-Q1,Revenue,10\n", "quarterly figures"),
])
def test_failures_say_what_to_fix(client, raw, phrase):
    conn, _, result = _sync(raw)
    conn.close()
    assert result["status"] == "error" and phrase in result["error"]


def test_a_private_link_is_explained(client, monkeypatch):
    import urllib.error

    def denied(*_a, **_k):
        raise urllib.error.HTTPError("https://example.com/f.csv", 403, "Forbidden", {}, None)

    monkeypatch.setattr(connectors, "public_urlopen", denied)
    with pytest.raises(connectors.SyncError, match="shared publicly"):
        connectors.fetch_bytes("https://example.com/f.csv")
