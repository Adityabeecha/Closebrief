"""P7: Settings -> Activity names what each entry is about, and the screen's
wording and link parsing for linked sources."""

import subprocess
from pathlib import Path

import pytest
from test_auth import make_client  # noqa: F401  (make_client is a pytest fixture)
from test_digest_approved import _seed

SOURCE = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")


def test_activity_names_the_kpi_and_close_for_commentary(make_client):  # noqa: F811
    client = make_client(auth_on=False)
    _, ids = _seed(approve_all=False)
    from app.db import get_connection
    conn = get_connection()
    rid = conn.execute("SELECT id FROM generated_reports WHERE metric_id = ?", (ids["Cost"],)).fetchone()["id"]
    conn.close()
    assert client.post(f"/reports/{rid}/review", json={"status": "approved"}).status_code == 200
    entries = client.get("/audit").json()
    hit = next(e for e in entries if e["entity_type"] == "report" and str(e["entity_id"]) == str(rid))
    assert (hit["action"], hit["metric"], hit["period"]) == ("approved", "Cost", "2026-06")
    assert client.get("/audit/verify").json()["ok"] is True


def _js(expr):
    start = SOURCE.index("/* ---------- Settings → Activity")
    end = SOURCE.index("async function loadSyncSources(", start)
    stubs = ("const esc=String,plural=(n,w,p)=>`${n} ${n===1?w:(p||w+'s')}`;"
             "const MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];"
             r"const fmtPeriod=p=>{const m=/^(\d{4})-(\d{2})$/.exec(p);return m?MONTHS[+m[2]-1]+' '+m[1]:p;};"
             "const fmtDate=s=>String(s).slice(0,10);")
    script = stubs + SOURCE[start:end] + f"\nconsole.log(JSON.stringify({expr}));"
    try:
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                             check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return out.stdout.strip()


def test_activity_rows_read_as_sentences():
    assert _js('describeActivity({action:"approved",entity_type:"report",metric:"Net Revenue",period:"2025-12"})') == \
        '{"text":"Approved commentary","about":"Net Revenue, December 2025"}'
    assert _js('describeActivity({action:"reopened",entity_type:"report"}).text') == '"Reopened commentary"'
    assert _js('describeActivity({action:"sync",entity_type:"connector",summary:{status:"ok",rows:240}})') == \
        '{"text":"Synced linked source","about":"240 rows"}'
    assert _js('describeActivity({action:"login",entity_type:"user"}).text') == '"Signed in"'
    assert _js('describeActivity({action:"something_new",entity_type:"widget"}).text') == '"Something new widget"'


def test_linked_source_links_are_parsed_and_unsafe_ones_refused():
    assert _js('syncSourceFrom("https://docs.google.com/spreadsheets/d/1AbC_x-9/edit#gid=42")') == \
        '{"kind":"google_sheets","config":{"sheet_id":"1AbC_x-9","gid":"42"}}'
    assert _js('syncSourceFrom("https://docs.google.com/spreadsheets/d/XYZ/edit")') == \
        '{"kind":"google_sheets","config":{"sheet_id":"XYZ","gid":"0"}}'
    assert _js('syncSourceFrom("https://example.com/actuals.csv").kind') == '"csv_url"'
    assert _js('[syncSourceFrom("http://example.com/a.csv"),syncSourceFrom("file:///etc/passwd"),syncSourceFrom("")]') == \
        "[null,null,null]"
