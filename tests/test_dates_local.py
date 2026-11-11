"""U4: timestamps show the viewer's local date; bare calendar dates stay as written."""

import os
import subprocess
from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")


def _fn(name):
    start = SOURCE.index(f"function {name}(")
    depth, i = 0, SOURCE.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(SOURCE[i], 0)
        i += 1
        if depth == 0:
            return SOURCE[start:i]


def _run(tz, expr):
    script = ('const MONTHS=["January","February","March","April","May","June","July","August",'
              '"September","October","November","December"];\n' + _fn("fmtDate") + f"\nconsole.log({expr});")
    try:
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                             check=True, env={**os.environ, "TZ": tz}, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return out.stdout.strip()


def test_utc_timestamp_without_zone_shows_local_date_in_india():
    # 20:00 UTC on 10 Nov is 01:30 on 11 Nov in India.
    assert _run("Asia/Kolkata", 'fmtDate("2026-11-10 20:00:00")') == "11 Nov 2026"
    assert _run("Asia/Kolkata", 'fmtDate("2026-11-10T20:00:00Z")') == "11 Nov 2026"
    assert _run("Asia/Kolkata", 'fmtDate("2026-11-10T20:00:00.123+00:00")') == "11 Nov 2026"


def test_utc_timestamp_shows_previous_day_west_of_utc():
    assert _run("America/Los_Angeles", 'fmtDate("2026-11-10 03:00:00")') == "09 Nov 2026"


def test_bare_dates_are_calendar_days_in_every_zone():
    for tz in ("Asia/Kolkata", "America/Los_Angeles", "UTC"):
        assert _run(tz, 'fmtDate("2026-11-01")') == "01 Nov 2026"


def test_missing_or_garbage_is_a_dash():
    assert _run("UTC", '[fmtDate(null),fmtDate(""),fmtDate("soon")].join("|")') == "—|—|—"
