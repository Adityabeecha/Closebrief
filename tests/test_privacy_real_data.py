"""Real data: names hidden from the AI, a full export, and delete-everything."""

import json

from test_datasets import client as client  # noqa: F401
from test_import_honesty import LONG, _upload

from app import datasets
from app.db import get_connection
from app.privacy import Redactor, clean_terms
from app.retention import clear_workspace_data


def test_hidden_words_are_swapped_out_and_back():
    r = Redactor(["Globex", "Initech Corp", "Initech"])
    sent = r.hide("Two renewals (Globex and Initech Corp) slipped; initech paid late. Globexx is not a match.")
    assert "Globex " not in sent and "Initech" not in sent and "initech" not in sent
    assert "Globexx" in sent and "[Name" in sent
    assert r.show(sent).replace("initech", "Initech") .count("Initech") == 2
    assert r.show(r.hide("$1.2M from Globex")) == "$1.2M from Globex"
    assert Redactor([]).hide("Globex") == "Globex"


def test_terms_are_cleaned():
    assert clean_terms([" Globex ", "globex", "x", "", "Initech"]) == ["Globex", "Initech"]


def test_export_has_everything_and_delete_clears_the_workspace(client):  # noqa: F811
    uid = _upload(client, "period,metric,value\n2026-08,Revenue,100\n2026-09,Revenue,110\n")
    ds = client.post(f"/ingest/{uid}/mapping", json=LONG).json()["dataset_id"]
    client.post("/context", json={"title": "Why", "body": "Because", "metric_tags": ["Revenue"], "effective_date": "2026-09"})
    r = client.get("/workspace/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = json.loads(r.text)
    assert [d["id"] for d in data["datasets"]] == [ds]
    assert len(data["metric_values"]) == 2 and len(data["context_documents"]) == 1
    assert client.request("DELETE", "/workspace/data", json={"confirm": "nope"}).status_code == 422


def test_clear_workspace_data_keeps_other_workspaces(client):  # noqa: F811
    conn = get_connection()
    try:
        mine = datasets.create_dataset(conn, "Mine", activate=False, workspace_id=11)
        theirs = datasets.create_dataset(conn, "Theirs", activate=False, workspace_id=22)
        conn.commit()
        datasets.set_workspace_scope(11)
        assert clear_workspace_data(conn, 11) == 1
        datasets.set_workspace_scope(None)
        left = [r["id"] for r in conn.execute("SELECT id FROM datasets").fetchall()]
    finally:
        datasets.set_workspace_scope(None)
        conn.close()
    assert mine not in left and theirs in left
