"""Phase 6 screens: an anomaly says why it was flagged, the reason rule follows the
dataset's threshold, and Links between datasets is gone."""

import json
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "ui" / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
HOME = (WEB / "home.html").read_text(encoding="utf-8")


def _js(expr, pre=""):
    s = JS.index('// "Big" is the same rule the close checklist uses')
    e = JS.index("function renderWhyNone(", s)
    try:
        out = subprocess.run(["node", "-"], input=pre + JS[s:e] + f"\nconsole.log(JSON.stringify({expr}));",
                             capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return json.loads(out.stdout)


def test_anomaly_says_how_it_was_judged():
    seasonal = _js("anomalyWhy({is_anomaly:true,anomaly:{basis:'seasonal',z:-2.77,threshold:2}})")
    assert "same month last year by 2.8× the usual spread (flagged above 2×)" in seasonal
    mom = _js("anomalyWhy({is_anomaly:true,anomaly:{basis:'month_on_month',z:3.1,threshold:2}})")
    assert "3.1× the usual month-to-month spread" in mom and "a year of history" in mom
    assert _js("anomalyWhy({is_anomaly:false,anomaly:{basis:'seasonal',z:1,threshold:2}})") == ""


def test_reason_rule_uses_the_server_flag_then_the_dataset_threshold():
    pre = "let activeDatasetObj={reason_threshold_pct:25};"
    assert _js("[needsReason({has_data:true,needs_reason_flag:true}),needsReason({has_data:true,needs_reason_flag:false,is_anomaly:true})]",
               pre) == [True, False]
    # Without the server's flag: 15% over plan is under a 25% threshold, an anomaly always counts.
    assert _js("[needsReason({has_data:true,deltas:{budget_var_pct:15}}),needsReason({has_data:true,deltas:{budget_var_pct:30}}),"
               "needsReason({has_data:true,is_anomaly:true,deltas:{}}),needsReason({has_data:true,sources:[1],deltas:{budget_var_pct:30}})]",
               pre) == [False, True, True, False]
    assert "Needs a reason above (" in JS and "reason_threshold_pct:v" in JS
    assert "Math.abs(f.deltas?.budget_var_pct||0)>10" not in JS


def test_links_between_datasets_is_gone():
    for gone in ("Links between datasets", "loadCrossDomain", "xd-list", "/insights/cross-domain"):
        assert gone not in JS and gone not in HTML, gone


def test_home_page_says_what_closebrief_is():
    assert "A month-end commentary tool." in HOME
    assert "isn't a planning, budgeting or BI tool" in HOME
    assert "Tested like a control" not in HOME and "traced to its source" not in HOME
