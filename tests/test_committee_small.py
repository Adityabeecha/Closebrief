"""Buyer committee regressions, exercised through real exports and UI functions."""

import pytest
from test_approval_link import seeded as approval_client  # noqa: F401
from test_datasets import client  # noqa: F401
from test_frontend_followup import run_js
from test_phase5_backend import _book, seeded  # noqa: F401
from test_secondary_screens import L, run_screen, section

from app.db import get_connection

WARNING = ("Check the units: this dataset says thousands or millions but amounts are stored as entered. "
           "Set 'Amounts are in' from the dataset menu.")


def _name_dataset(ds, name, scale, confirmed=False):
    conn = get_connection()
    try:
        conn.execute("UPDATE datasets SET name=?, amount_scale=?, currency='USD', units_confirmed=? WHERE id=?",
                     (name, scale, confirmed, ds))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("name", ["$ thousands", "$ 000", "Revenue (000s)", "MILLIONS"])
def test_export_is_blocked_until_the_units_are_confirmed(seeded, name):  # noqa: F811
    api, ds, _ = seeded
    _name_dataset(ds, name, 1)
    blocked = api.get("/close/export.xlsx", params={"period": "2025-12"})
    assert blocked.status_code == 409 and "Confirm the units first" in blocked.json()["detail"]
    # Keeping "units" is a valid answer: once confirmed, the export goes out clean.
    assert api.patch(f"/datasets/{ds}", json={"amount_scale": 1}).status_code == 200
    _name_dataset(ds, "Café:/Close?", 1, confirmed=True)   # leave the shared fixture as it was


@pytest.mark.parametrize("scale,confirmed", [(1000, False), (1, True)])
def test_a_confirmed_export_has_no_warning_and_keeps_usd(seeded, scale, confirmed):  # noqa: F811
    api, ds, _ = seeded
    _name_dataset(ds, "$ thousands", scale, confirmed)
    try:
        sheet = _book(api)["KPIs"]
    finally:
        _name_dataset(ds, "Café:/Close?", 1, confirmed=True)
    assert sheet["A1"].value == "Category"
    assert WARNING not in [row[0].value for row in sheet]
    headers = [cell.value for cell in sheet[1]]
    rows = [dict(zip(headers, [c.value for c in row])) for row in sheet.iter_rows(min_row=2)]
    revenue = next(r for r in rows if r["KPI"] == "Revenue")
    assert revenue["Unit"] == "USD"
    assert revenue["Actual"] == 1000  # Export never rescales computed amounts.


CONTRIB_SETUP = section("const fmt=", "/* ---------- reusable modal") + """
const r={available:true,dimension:'department',unit:'USD',
  members:[{member:'A',value:17323,budget:17001,var_abs:322},
           {member:'B',value:930000,budget:792000,var_abs:138000}],
  total:{value:947323,budget:809001,var_abs:138322}};
"""


def test_breakdown_uses_compact_amounts_and_preserves_other_units():
    run_js("function contributorsHtml(", "function metricHash(", CONTRIB_SETUP, r"""
      let html=contributorsHtml(r,{});
      assert.match(html,/\$17\.3K/);assert.match(html,/\$930K/);assert.match(html,/\$792K/);
      assert.doesNotMatch(html,/\$17,323/);
      displayCurrency='EUR';html=contributorsHtml(r,{});assert.match(html,/€17\.3K/);
      assert.equal(fmt(17323,'USD'),'€17,323'); // Other screens retain their format.
      r.unit='count';html=contributorsHtml(r,{});assert.match(html,/17,323/);assert.doesNotMatch(html,/17\.3K/);
    """)


def test_breakdown_rounding_note_only_when_displayed_parts_differ():
    run_js("function contributorsHtml(", "function metricHash(", CONTRIB_SETUP, """
      const note='Parts may not add to the total exactly because of rounding.';
      r.members=[{member:'A',value:17360},{member:'B',value:17360}];r.total={value:34720};
      assert.ok(contributorsHtml(r,{}).includes(note));
      r.members=[{member:'A',value:17000},{member:'B',value:18000}];r.total={value:35000};
      assert.ok(!contributorsHtml(r,{}).includes(note));
      r.members=[{member:'A',value:-17360},{member:'B',value:-17360}];r.total={value:-34720};
      assert.ok(contributorsHtml(r,{}).includes(note));
      r.members=[{member:'A',value:17000,budget:17360,var_abs:-360},
                 {member:'B',value:18000,budget:17360,var_abs:640}];
      r.total={value:35000,budget:34720,var_abs:280};
      assert.ok(contributorsHtml(r,{}).includes(note));
    """)


@pytest.mark.parametrize("locale", ["de-DE", "ar-EG"])
def test_breakdown_rounding_handles_localized_numbers(locale):
    locale_setup = """
const nativeLocaleString=Number.prototype.toLocaleString;
Number.prototype.toLocaleString=function(locale,options){return nativeLocaleString.call(this,locale||LOCALE,options)};
const NativeNumberFormat=Intl.NumberFormat;
Intl.NumberFormat=function(locale,options){return new NativeNumberFormat(locale||LOCALE,options)};
""".replace("LOCALE", repr(locale))
    run_js("function contributorsHtml(", "function metricHash(", CONTRIB_SETUP + locale_setup, r"""
      const note='Parts may not add to the total exactly because of rounding.';
      r.members=[{member:'A',value:1.5},{member:'B',value:1.5}];r.total={value:3};
      let html=contributorsHtml(r,{});assert.ok(!html.includes(note));
      r.members=[{member:'A',value:500},{member:'B',value:500}];r.total={value:1000};
      assert.ok(!contributorsHtml(r,{}).includes(note));
      r.members=[{member:'A',value:17360},{member:'B',value:17360}];r.total={value:34720};
      assert.ok(contributorsHtml(r,{}).includes(note));
    """)


@pytest.mark.parametrize("faithfulness,label", [("passed", "Numbers match the data"),
                                               ("failed", "A number doesn't match the data")])
def test_approval_page_uses_plain_number_check_labels(approval_client, faithfulness, label):  # noqa: F811
    conn = get_connection()
    try:
        conn.execute("UPDATE generated_reports SET faithfulness=?", (faithfulness,))
        conn.commit()
    finally:
        conn.close()
    token = approval_client.post("/close/approval-link", json={"period": "2026-09"}).json()["url"].split("t=", 1)[1]
    assert label in approval_client.get(f"/approve?t={token}").text


def test_plan_explanation_rule_and_prompt_version():
    from app.generation.prompts import PROMPT_VERSION, SYSTEM_PROMPT

    assert PROMPT_VERSION == "v9-close-commentary"
    assert ("Explain a variance vs plan with what differed from the plan (volume, price, timing, one-offs, the notes). "
            "Year-on-year growth is not a reason for beating or missing plan; mention it only as extra colour, "
            "in its own sentence.") in SYSTEM_PROMPT


@pytest.mark.parametrize("loader,box", [("loadContext", "ctx-rows"), ("loadDigest", "digest-list")])
def test_hung_tabs_offer_retry_and_ignore_the_old_request(loader, box):
    run_screen(L[loader], f"""
      let resolveOld;response=new Promise(r=>resolveOld=r);
      const pending={loader}();
      assert.doesNotMatch($('{box}').innerHTML,/server may be waking up/);
      const timeout=[...timers.values()].find(t=>t.ms===15000);assert.ok(timeout);
      timeout.f();const html=$('{box}').innerHTML;
      assert.ok(html.includes('Still loading — the server may be waking up.'));
      assert.match(html,/Retry/);
      const handler=html.match(/onclick="([^"]+)"/)[1];
      response=[];await eval(handler.replaceAll('&#39;',"'").replaceAll('&quot;','"'));
      const afterRetry=$('{box}').innerHTML;assert.doesNotMatch(afterRetry,/Retry|Loading/);
      resolveOld([]);await pending;assert.equal($('{box}').innerHTML,afterRetry);
      assert.equal(timers.size,0);
    """)
