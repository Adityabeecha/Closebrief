"""A trial balance or GL export, imported as it comes out of the ERP.

A raw export is one row per account (and month): a code, a name, and either a net
amount or debit and credit columns. It isn't a list of KPIs: 400 accounts are not
400 board lines. Each account is mapped once to a P&L line (Revenue, Cost of
revenue, Sales & marketing, ...), the accounts are added up into those lines, and
each account is kept as a part of its line, so "which accounts moved" is one click.

The mapping is saved per workspace, so next month's export maps itself; only new
accounts need a decision. Suggestions come from the account code's range and its
name, and the analyst confirms them before anything is imported.
"""

from __future__ import annotations

import json
import re

import pandas as pd

EXCLUDE = "Exclude"
# Standard P&L lines. Income lines are credits in a trial balance (negative when
# debits are positive), so they're flipped to read as positive amounts.
LINES = ("Revenue", "Cost of revenue", "Sales & marketing", "Research & development",
         "General & administrative", "Other income", "Other expense", "Income tax")
INCOME_LINES = {"Revenue", "Other income"}
ACCOUNT_DIMENSION = "Account"

# First match wins, so the specific rules come first: "bank charges" is an expense
# before "bank" is a balance-sheet account, "sales commissions" is marketing before
# "sales" is revenue, and "deferred revenue" is a liability before it's revenue.
_NAME_RULES = (
    ("Income tax", r"\b(income tax|tax expense|provision for tax)"),
    ("Other income", r"\b(interest income|other income|gain on|dividend income|fx gain)"),
    ("Other expense", r"\b(interest expense|bank charges|loss on|fx loss|other expense)"),
    (EXCLUDE, r"\b(cash|bank|receivables?|payables?|inventory|prepaid|accrued (liabilit|expens)|"
              r"equity|retained earnings|capital|loan|deferred revenue|fixed assets?|accumulated|"
              r"goodwill|intangible assets|suspense|clearing)\b"),
    ("Cost of revenue", r"\b(cogs|cost of (goods|sales|revenue|services)|hosting|merchant fees|"
                        r"freight|direct (labou?r|material)|subcontract)"),
    ("Sales & marketing", r"\b(marketing|advertis|commission|sales (salaries|team|staff)|events?|"
                          r"lead gen|sponsorship|promotion|referral|partner fees|affiliate)"),
    ("Revenue", r"\b(revenue|sales|subscription|licen[cs]e fees?|services income|turnover|billings?)\b"),
    ("Research & development", r"\b(research|r&d|engineering|development|product (team|salaries))"),
    ("General & administrative", r"\b(rent|office|salar|wages|payroll|admin|legal|audit|accounting|"
                                 r"insurance|software|depreciation|amorti[sz]ation|utilities|travel|"
                                 r"professional fees|recruit|training|telephone|postage)"),
)


def suggest_line(code: str | None, name: str | None) -> str | None:
    """A P&L line for an account from its name, then its code's range (1-3 balance sheet,
    4 revenue, 5 cost of revenue, 6-7 operating expense, 8-9 other). None when unsure."""
    text = (name or "").lower()
    for line, pattern in _NAME_RULES:
        if re.search(pattern, text):
            return line
    digits = re.match(r"\s*(\d)", str(code or ""))
    if not digits:
        return None
    first = digits.group(1)
    return {"1": EXCLUDE, "2": EXCLUDE, "3": EXCLUDE, "4": "Revenue", "5": "Cost of revenue",
            "6": "General & administrative", "7": "General & administrative",
            "8": "Other expense", "9": "Other expense"}.get(first)


def account_key(code, name) -> str:
    """How an account is remembered: its code when there is one (names drift), else its name."""
    c = str(code).strip() if code is not None and str(code).strip() not in ("", "nan", "None") else ""
    return c or str(name or "").strip()


def account_label(code, name) -> str:
    c = account_key(code, None)
    n = str(name or "").strip()
    return f"{c} · {n}" if c and n and c != n else (n or c)


def net_amounts(df: pd.DataFrame, value_col: str | None, debit_col: str | None, credit_col: str | None,
                coerce) -> pd.Series:
    """The account's net amount: the amount column, or debit minus credit."""
    if debit_col or credit_col:
        dr = df[debit_col].map(coerce).fillna(0) if debit_col else 0
        cr = df[credit_col].map(coerce).fillna(0) if credit_col else 0
        return dr - cr
    return df[value_col].map(coerce)


def saved_map(conn, workspace_id) -> dict[str, str]:
    rows = conn.execute("SELECT account_key, line FROM account_maps WHERE workspace_id = ?",
                        (int(workspace_id or 0),)).fetchall()
    return {r["account_key"]: r["line"] for r in rows}


def save_map(conn, workspace_id, account_map: dict[str, str]) -> None:
    ws = int(workspace_id or 0)
    for key, line in account_map.items():
        key, line = str(key).strip()[:200], str(line or "").strip()[:80]
        if not key or not line:
            continue
        conn.execute("DELETE FROM account_maps WHERE workspace_id = ? AND account_key = ?", (ws, key))
        conn.execute("INSERT INTO account_maps (workspace_id, account_key, line) VALUES (?, ?, ?)", (ws, key, line))


def list_accounts(df: pd.DataFrame, *, name_col: str | None, code_col: str | None, value_col: str | None,
                  debit_col: str | None, credit_col: str | None, saved: dict[str, str], coerce) -> list[dict]:
    """Each account once, with its total, rows and the line it will go to (saved, suggested or none)."""
    work = pd.DataFrame({
        "code": df[code_col].astype(str).str.strip() if code_col else None,
        "name": df[name_col].astype(str).str.strip() if name_col else None,
        "amount": net_amounts(df, value_col, debit_col, credit_col, coerce),
    })
    work["key"] = [account_key(c, n) for c, n in zip(work["code"], work["name"])]
    out = []
    for key, rows in work.groupby("key", sort=False):
        if not key:
            continue
        code, name = rows["code"].iloc[0], rows["name"].iloc[0]
        line, source = saved.get(key), "saved"
        if line is None:
            line, source = suggest_line(code, name), "suggested"
        if line is None:
            source = "none"
        out.append({"key": key, "code": None if code in (None, "nan") else code, "name": name,
                    "label": account_label(code, name), "total": float(rows["amount"].fillna(0).sum()),
                    "rows": int(len(rows)), "line": line, "source": source})
    out.sort(key=lambda a: (str(a["code"] or ""), str(a["name"] or "")))
    return out


def roll_up(out: pd.DataFrame, account_map: dict[str, str], *, income_flip: bool = True) -> tuple[pd.DataFrame, dict]:
    """Turn account rows (metric = account label, id = code) into P&L lines: each row's
    metric becomes its mapped line, the account is kept as the 'Account' part, income
    lines are flipped to positive, and excluded or unmapped accounts are left out."""
    keys = [account_key(i, m) for i, m in zip(out["id"] if "id" in out else [None] * len(out), out["metric"])]
    lines = [account_map.get(k) for k in keys]
    excluded = sorted({k for k, line in zip(keys, lines) if line == EXCLUDE})
    unmapped = sorted({k for k, line in zip(keys, lines) if not line})
    labels = [account_label(i, m) for i, m in zip(out["id"] if "id" in out else [None] * len(out), out["metric"])]

    def with_account(raw, label):
        dims = json.loads(raw) if isinstance(raw, str) and raw else {}
        return json.dumps({ACCOUNT_DIMENSION: label, **dims})

    out = out.copy()
    out["dimensions"] = [with_account(raw, label) for raw, label in zip(out["dimensions"], labels)]
    out["metric"] = lines
    out["id"] = None
    keep = [bool(line) and line != EXCLUDE for line in lines]
    out = out[keep].reset_index(drop=True)
    if income_flip:
        flip = out["metric"].isin(INCOME_LINES)
        for col in ("value", "budget", "forecast"):
            if col in out and out[col] is not None:
                out.loc[flip, col] = -pd.to_numeric(out.loc[flip, col], errors="coerce")
    return out, {"accounts_excluded": len(excluded), "accounts_unmapped": len(unmapped),
                 "unmapped_examples": unmapped[:20]}
