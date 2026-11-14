"""The draft knows what the comparison is called, the data's cadence and the house style."""

from test_datasets import client as client  # noqa: F401

from app.generation.prompts import build_user_prompt
from app.schemas import ComputedFact, Deltas
from app.style import clean_style, style_rules


def _fact(**kw):
    return ComputedFact(metric="Revenue", period="2026-09", value=947565, prior_value=911740,
                        deltas=Deltas(mom_pct=3.93, yoy_pct=17.05, budget_var_abs=138047, budget_var_pct=17.05), **kw)


def test_prompt_names_the_comparison_and_cadence():
    p = build_user_prompt(_fact(budget_label="prior year", cadence="quarter"), [])
    assert "'prior year'" in p and "vs prior year" in p
    assert "last quarter" in p and "never 'month'" in p
    assert "Cadence" not in build_user_prompt(_fact(), [])


def test_house_style_rules():
    st = clean_style({"favourable_wording": True, "max_sentences": 9, "forward_looking": False, "instruction": "Use $M"})
    assert st["max_sentences"] == 6
    rules = " ".join(style_rules(st))
    assert "favourable" in rules and "at most 6 sentences" in rules and "forward-looking" in rules and "Use $M" in rules
    assert "House style" in build_user_prompt(_fact(house_style=st), [])
    assert style_rules(None) == []


def test_dataset_comparison_label_is_saved(client):  # noqa: F811
    from test_import_honesty import LONG, _upload
    uid = _upload(client, "period,metric,value\n2026-08,Revenue,1\n")
    ds = client.post(f"/ingest/{uid}/mapping", json=LONG).json()["dataset_id"]
    assert client.patch(f"/datasets/{ds}", json={"budget_label": "x" * 41}).status_code == 422
    r = client.patch(f"/datasets/{ds}", json={"budget_label": "prior year"})
    assert r.status_code == 200
    body = client.get("/datasets").json()
    rows = body.get("datasets", body) if isinstance(body, dict) else body
    assert next(d for d in rows if d["id"] == ds)["budget_label"] == "prior year"
