"""Phase 3 screens: one-person workspaces approve their own commentary with no
reviewer assignment (F13), forecast and what-if read as quick estimates with a
range (F15), and the figures seal says what it does not prove (F17)."""

import json
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "ui" / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


def test_no_reviewer_assignment_or_review_queue():
    for gone in ("Assign reviewer", "toggleAssign", "assignReviewer", "review-queue", "rq-list", "review-badge"):
        assert gone not in JS and gone not in HTML, gone
    # Old links to the review screen still land on the overview.
    assert 'review:{tab:"overview"}' in JS


def test_forecast_and_what_if_say_quick_estimate():
    assert HTML.count('<span class="est-tag">Quick estimate</span>') == 2
    assert "not a plan" in HTML and "not a driver-based model" in HTML
    assert "Typical error</div>" not in JS and "Likely range" in JS


def test_forecast_range_is_next_period_plus_or_minus_past_error():
    s = JS.index("function fcRange(")
    e = JS.index("async function runForecast(", s)
    src = "const fmt=(v,u)=>String(Math.round(v));" + JS[s:e]
    expr = "[fcRange(1000,10,'USD'),fcRange(-200,5,'USD'),fcRange(null,10),fcRange(1000,null)]"
    try:
        out = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"],
                             capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    assert json.loads(out.stdout) == ["900 – 1100", "-210 – -190", "–", "–"]


def test_figures_seal_says_reasons_are_not_verified():
    assert "The reasons come from your context notes and are not verified." in JS
