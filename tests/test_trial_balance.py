"""A raw trial balance imports without reformatting: accounts map to P&L lines (saved
for next month), debit/credit nets, income reads positive, balance sheet is left out,
and each account is kept as a part of its line."""

from test_datasets import client as client  # noqa: F401
from test_import_honesty import _upload

from app.db import get_connection
from app.ingestion.ledger import EXCLUDE, suggest_line

TB = (
    "Period,Account Code,Account Name,Debit,Credit\n"
    "2026-08,1000,Cash at bank,500000,\n"
    "2026-08,1200,Accounts receivable,250000,\n"
    "2026-08,2000,Accounts payable,,180000\n"
    "2026-08,4000,Subscription revenue,,900000\n"
    "2026-08,4100,Professional services revenue,,100000\n"
    "2026-08,5000,Hosting,120000,\n"
    "2026-08,6100,Advertising,80000,\n"
    "2026-08,6200,Sales commissions,40000,\n"
    "2026-08,6500,Office rent,30000,\n"
    "2026-09,1000,Cash at bank,520000,\n"
    "2026-09,4000,Subscription revenue,,950000\n"
    "2026-09,4100,Professional services revenue,,90000\n"
    "2026-09,5000,Hosting,125000,\n"
    "2026-09,6100,Advertising,95000,\n"
    "2026-09,6200,Sales commissions,42000,\n"
    "2026-09,6500,Office rent,30000,\n"
)
COLS = {"name_col": "Account Name", "code_col": "Account Code", "debit_col": "Debit", "credit_col": "Credit"}
MAP = {"layout": "long", "period_col": "Period", "metric_col": "Account Name", "id_col": "Account Code",
       "debit_col": "Debit", "credit_col": "Credit"}


def test_suggestions_from_names_and_code_ranges():
    assert suggest_line("4000", "Subscription revenue") == "Revenue"
    assert suggest_line("1200", "Accounts receivable") == EXCLUDE
    assert suggest_line("5000", "Hosting") == "Cost of revenue"
    assert suggest_line("6100", "Advertising") == "Sales & marketing"
    assert suggest_line("6500", "Office rent") == "General & administrative"
    assert suggest_line("7300", "Misc 7300") == "General & administrative"   # by code range
    assert suggest_line(None, "Something odd") is None
    assert suggest_line("6200", "Sales commissions") == "Sales & marketing"   # not revenue
    assert suggest_line("4200", "Product sales") == "Revenue"
    assert suggest_line("8100", "Bank charges") == "Other expense"            # not a bank account
    assert suggest_line("2400", "Deferred revenue") == EXCLUDE
    assert suggest_line("6250", "Partner referral fees") == "Sales & marketing"


def test_a_trial_balance_imports_as_pl_lines(client):  # noqa: F811
    uid = _upload(client, TB)
    r = client.post(f"/ingest/{uid}/accounts", json=COLS)
    assert r.status_code == 200, r.text
    body = r.json()
    accounts = {a["key"]: a for a in body["accounts"]}
    assert accounts["4000"]["line"] == "Revenue" and accounts["4000"]["total"] == -1_850_000
    assert accounts["1000"]["line"] == EXCLUDE and body["unmapped"] == 0
    account_map = {k: a["line"] for k, a in accounts.items()}

    r = client.post(f"/ingest/{uid}/mapping", json={**MAP, "account_map": account_map})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["metrics"]) == ["Cost of revenue", "General & administrative", "Revenue", "Sales & marketing"]
    assert r.json()["import_report"]["ledger"]["accounts_excluded"] == 3

    facts = {f["metric"]: f for f in client.get("/facts?period=2026-09").json()}
    assert facts["Revenue"]["value"] == 1_040_000          # credits read as positive income
    assert facts["Sales & marketing"]["value"] == 137_000  # two accounts added up
    assert facts["Cost of revenue"]["value"] == 125_000

    conn = get_connection()
    try:
        parts = conn.execute("""SELECT mb.member, mb.value FROM metric_breakdowns mb JOIN metrics m ON m.id = mb.metric_id
                                WHERE m.name = 'Revenue' AND mb.period = '2026-09' AND mb.dimension = 'Account'
                                ORDER BY mb.member""").fetchall()
    finally:
        conn.close()
    assert [(p["member"], p["value"]) for p in parts] == [
        ("4000 · Subscription revenue", 950_000), ("4100 · Professional services revenue", 90_000)]


def test_next_months_export_maps_itself(client):  # noqa: F811
    uid = _upload(client, TB)
    accounts = client.post(f"/ingest/{uid}/accounts", json=COLS).json()["accounts"]
    account_map = {a["key"]: a["line"] for a in accounts}
    account_map["6500"] = "Facilities"          # the team's own line name is kept too
    client.post(f"/ingest/{uid}/mapping", json={**MAP, "account_map": account_map})

    nxt = _upload(client, TB.replace("2026-09", "2026-10").replace("2026-08", "2026-09")
                  + "2026-10,6900,Team offsite,15000,\n")
    body = client.post(f"/ingest/{nxt}/accounts", json=COLS).json()
    acc = {a["key"]: a for a in body["accounts"]}
    assert acc["6500"]["line"] == "Facilities" and acc["6500"]["source"] == "saved"
    assert acc["6900"]["source"] == "suggested"   # only the new account needs a look
    assert body["saved"] == len(acc) - 1


def test_unmapped_accounts_are_left_out_and_counted(client):  # noqa: F811
    uid = _upload(client, TB)
    r = client.post(f"/ingest/{uid}/mapping", json={**MAP, "account_map": {"4000": "Revenue"}})
    assert r.status_code == 200, r.text
    assert r.json()["metrics"] == ["Revenue"]
    assert r.json()["import_report"]["ledger"]["accounts_unmapped"] == 8


def test_accounts_need_columns(client):  # noqa: F811
    uid = _upload(client, TB)
    assert client.post(f"/ingest/{uid}/accounts", json={"debit_col": "Debit"}).status_code == 422
    assert client.post(f"/ingest/{uid}/accounts", json={"name_col": "Nope", "value_col": "Debit"}).status_code == 422
