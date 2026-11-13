"""Excel close pack: presentation of shared facts, never a second KPI calculator."""

import math
import re
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from app.board_pack import _period_title
from app.compute.close_views import contributors
from app.currency import symbol
from app.datasets import dataset_metadata
from app.digest.digest import last_sent_at
from app.import_receipts import list_receipts

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _text(cell, value):
    """Every text cell is an explicit string, including formula-like imported text."""
    cell.value = value
    cell.data_type = "s"


def _row(sheet, values):
    index = sheet.max_row + 1 if sheet.cell(1, 1).value is not None else 1
    cells = []
    for column, value in enumerate(values, 1):
        cell = sheet.cell(index, column)
        if isinstance(value, str):
            _text(cell, value)
        else:
            cell.value = None if isinstance(value, float) and not math.isfinite(value) else value
        cells.append(cell)
    return cells


def _sheet(book, name, headers):
    sheet = book.create_sheet(name)
    for cell in _row(sheet, headers):
        cell.font = Font(bold=True)
        sheet.column_dimensions[cell.column_letter].width = max(18, len(cell.value) + 2)
    sheet.freeze_panes = "A2"
    return sheet


def _format(unit, currency, variance=False):
    if variance and unit == "%":
        return '0.0" pts"'
    if variance and unit == "days":
        return '0.0" days"'
    if unit == "%":
        return "0.0%"
    if unit == "days":
        return "0.0"
    if unit == "count":
        return "#,##0"
    if unit == "number":
        return "#,##0.##"
    return f'"{symbol(currency)}"#,##0' + ("" if currency == "JPY" else ".00")


def _number(value, unit=None, relative=False):
    if value is None or not math.isfinite(float(value)):
        return None
    return value / 100 if relative or unit == "%" else value


def _review(fact):
    if not fact["has_data"]:
        return "No data", "—"
    if fact["report_id"] is None:
        return "Not drafted", "—"
    status = "Approved" if fact["review_status"] == "approved" else "Draft"
    check = {"passed": "Checked", "unchecked": "Changed since written"}.get(fact["faithfulness"], "Needs review")
    return status, check


def _kpis(book, facts, granularity, currency):
    forecast = any(f["forecast"] is not None for f in facts)
    ytd = granularity == "month" and any(f["ytd"] is not None for f in facts)
    columns = ["Category", "KPI", "Unit", "Actual", "Plan", "Variance", "Variance %"]
    if forecast:
        columns += ["Forecast", "Vs forecast %"]
    columns += ["Prior period", "Vs prior %", "Vs last year %"]
    if ytd:
        columns += ["YTD actual", "YTD plan", "YTD vs plan %", "YTD vs last year %"]
    columns += ["Status", "Figures check", "Commentary"]
    sheet = _sheet(book, "KPIs", columns)
    sheet.freeze_panes = "C2"
    for fact in facts:
        unit, delta, annual = fact["unit"], fact["deltas"], fact["ytd"] or {}
        status, check = _review(fact)
        values = {
            "Category": fact["category"], "KPI": fact["metric"], "Unit": unit,
            "Actual": fact["value"], "Plan": fact["budget"], "Variance": delta["budget_var_abs"],
            "Variance %": delta["budget_var_pct"], "Forecast": fact["forecast"],
            "Vs forecast %": delta["forecast_var_pct"], "Prior period": fact["prior_value"],
            "Vs prior %": delta["mom_pct"], "Vs last year %": delta["yoy_pct"],
            "YTD actual": annual.get("value"), "YTD plan": annual.get("budget"),
            "YTD vs plan %": annual.get("var_pct"), "YTD vs last year %": annual.get("yoy_pct"),
            "Status": status, "Figures check": check,
            "Commentary": fact["narrative"] if status == "Approved" else None,
        }
        formats = {}
        for column in columns[3:-3]:
            relative = column.endswith("%")
            value = None if relative and unit == "%" else values[column]
            # A percentage's absolute variance remains in points, not a fraction.
            values[column] = _number(value, unit=unit if column != "Variance" else None, relative=relative)
            formats[column] = "0.0%" if relative else _format(unit, currency, variance=column == "Variance")
        for column, cell in zip(columns, _row(sheet, [values[c] for c in columns])):
            if column in formats:
                cell.number_format = formats[column]
            if column == "Commentary":
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    for name, width in {"KPI": 30, "Category": 22, "Figures check": 26, "Commentary": 80}.items():
        sheet.column_dimensions[get_column_letter(columns.index(name) + 1)].width = width
    sheet.auto_filter.ref = sheet.dimensions


def _contributors(book, conn, facts, period, covered, currency):
    if not any(f["has_breakdown"] for f in facts):
        return
    sheet = _sheet(book, "Contributors", ["KPI", "Breakdown", "Part", "Actual", "Plan", "Variance"])
    for fact in facts:
        if not fact["has_breakdown"]:
            continue
        metric = {"id": fact["metric_id"], "name": fact["metric"]}
        result = contributors(conn, metric, period, covered, fact["aggregation_type"], fact["unit"])
        for member in result["members"]:
            cells = _row(sheet, [fact["metric"], result["dimension"], member["member"],
                                 member["value"], member["budget"], member["var_abs"]])
            for cell in cells[3:]:
                cell.number_format = _format(fact["unit"], currency, variance=cell.column == 6)
    sheet.auto_filter.ref = sheet.dimensions


def _receipts(book, conn, ds):
    receipts = list_receipts(conn, ds)
    sheet = _sheet(book, "Receipt", ["When", "Kind", "File", "Rows read", "Rows kept"])
    for receipt in receipts:
        report = receipt["report"]
        _row(sheet, [receipt["created_at"], receipt["kind"], receipt["filename"],
                     report.get("rows_read"), report.get("rows_kept")])
    if receipts:
        _row(sheet, [None])
        for cell in _row(sheet, ["KPI", "Months", "Actual total", "Budget total", "Rows"]):
            cell.font = Font(bold=True)
        for total in receipts[0]["kpi_totals"]:
            _row(sheet, [total["metric"], total["periods"], total["value_total"], total["budget_total"], total["rows"]])
    sheet.column_dimensions["A"].width = 30
    sheet.column_dimensions["C"].width = 45


def build_close_workbook(conn, ds, name, period, granularity, covered, facts, summary, changes):
    meta = dataset_metadata(conn, ds)
    book = Workbook()
    book.remove(book.active)
    sheet = book.create_sheet("Summary")
    summary = summary or {}
    status = {"not_started": "Not started", "in_progress": "In progress", "shared": "Shared"}.get(
        summary.get("status"), "Not started")
    if status != "Shared" and summary.get("kpis") and summary["approved"] == summary["kpis"]:
        status = "Ready"
    rows = [
        ("Close", f"{_period_title(period)} close"), ("Dataset", name), ("Currency", meta["currency"]),
        ("Fiscal year start month", meta["fiscal_year_start"]), ("Data as of", meta["data_changed_at"]),
        ("Close status", status),
    ]
    if summary.get("locked"):
        rows.append(("Lock", f"Locked on {summary['locked_at']} by {summary['locked_by']}"))
    rows += [("Digest sent date", last_sent_at(conn, ds, period) or summary.get("shared_at"))]
    rows += [(label, summary.get(key, 0)) for label, key in [
        ("KPIs", "kpis"), ("Drafted", "drafted"), ("Approved", "approved"),
        ("Figures checked", "checked"), ("Needs a reason", "needs_reason")]]
    rows += [("Generated at (UTC)", datetime.now(timezone.utc).isoformat())]
    for row in rows:
        _row(sheet, row)[0].font = Font(bold=True)
    _row(sheet, ["Figures are computed in code. Commentary is included only once approved."])
    sheet.column_dimensions["A"].width = 28
    sheet.column_dimensions["B"].width = 85
    _kpis(book, facts, granularity, meta["currency"])
    _contributors(book, conn, facts, period, covered, meta["currency"])
    if changes:
        sheet = _sheet(book, "Changes", ["When", "KPI", "Month", "Field", "Old", "New", "Source", "Who"])
        for change in changes:
            _row(sheet, [change[k] for k in ("at", "metric", "period", "field", "old_value", "new_value", "source", "actor")])
        sheet.auto_filter.ref = sheet.dimensions
    _receipts(book, conn, ds)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def content_disposition(name, period):
    filename = f"CloseBrief - {name} - {_period_title(period)} close.xlsx"
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "", filename)
    fallback = filename.encode("ascii", "ignore").decode("ascii")
    header = f'attachment; filename="{fallback}"'
    if filename != fallback:
        header += "; filename*=UTF-8''" + quote(filename, safe="")
    return header
