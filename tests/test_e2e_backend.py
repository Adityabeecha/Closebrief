import pandas as pd
import pytest

from app.close_export import _number
from app.ingestion.profiler import profile_columns, suggest_mapping
from app.kpis.library import suggest_import_defaults


@pytest.mark.parametrize(
    "name,unit,direction,agg",
    [
        ("Research & Development", "USD", "down", "flow"),
        ("R&D", "USD", "down", "flow"),
        ("General & Administrative", "USD", "down", "flow"),
        ("Sales & Marketing", "USD", "down", "flow"),
        ("S&M", "USD", "down", "flow"),
        ("Cost of Revenue", "USD", "down", "flow"),
        ("Operating Expenses", "USD", "down", "flow"),
        ("Opex", "USD", "down", "flow"),
        ("Net Revenue Retention", "%", "up", "ratio"),
        ("DSO", "days", "down", "ratio"),
        ("Days Sales Outstanding", "days", "down", "ratio"),
        ("G&A", "USD", "down", "flow"),
        ("ARR", "USD", "up", "balance"),
        ("MRR", "USD", "up", "balance"),
        ("NRR", "%", "up", "ratio"),
        ("DSO (days)", "days", "down", "ratio"),
        ("DPO", "days", "up", "ratio"),
        ("Logo Churn %", "%", "down", "ratio"),
        ("Ending Headcount", "count", "up", "balance"),
    ],
)
def test_defaults(name, unit, direction, agg):
    d = suggest_import_defaults(name)
    assert (d["unit"], d["direction_good"], d["aggregation_type"]) == (unit, direction, agg)


@pytest.mark.parametrize("metrics,regions", [(5, 1), (1, 3)])
def test_valid_long(metrics, regions):
    df = pd.DataFrame(
        [
            {"period": str(p), "Metric": f"KPI {k}", "region": f"Region {r}", "value": 100}
            for p in pd.period_range("2024-01", periods=22, freq="M")
            for k in range(metrics)
            for r in range(regions)
        ]
    )
    warnings = " ".join(suggest_mapping(profile_columns(df))["warnings"])
    assert "roster" not in warnings
    assert "collapse everything" not in warnings


def test_export_rounding():
    assert _number(14356.1800000001) == 14356.18
    assert _number(12.345678, relative=True) == 0.1235


@pytest.mark.parametrize(
    "text",
    [
        "This marks the third consecutive month of growth.",
        "This continues a 21-month growth streak.",
        "It has been increasing for three months.",
        "It has risen for twenty-four months.",
    ],
)
def test_streak_not_duplicated(text):
    from app.generation.generate import _without_context_closing
    from app.schemas import ComputedFact, Deltas, TrendStreak

    fact = ComputedFact(metric="Revenue", period="2026-01", value=100, deltas=Deltas())
    streak = TrendStreak(metric="Revenue", direction="growing", months=3, start_period="2025-11", end_period="2026-01")
    assert _without_context_closing(text, fact, streak) == text


def test_prompt_rules_and_identifier_cleanup():
    from app.generation.prompts import PROMPT_VERSION, QA_SYSTEM_PROMPT, SYSTEM_PROMPT
    from app.generation.qa import clean_answer

    assert PROMPT_VERSION == "v8-close-commentary"
    assert "rise is unfavourable" in SYSTEM_PROMPT
    assert "continued growth" in SYSTEM_PROMPT
    assert "Never mention snippet ids" in QA_SYSTEM_PROMPT
    assert clean_answer('Hiring slowed as noted in context snippet id "ctx_019".') == "Hiring slowed."
    assert "ctx_123" not in clean_answer("See ctx_123 for details.")


def test_timestamp_and_formats():
    from app.close_export import _format, _timestamp

    assert _timestamp("2026-11-13T15:20:51.493640Z") == "13 Nov 2026 15:20 UTC"
    assert _timestamp("2026-11-13 15:35:17.144041+00:00") == "13 Nov 2026 15:35 UTC"
    assert _format("USD", "USD") == "#,##0.00"
    assert _format("%", "USD") == "0.0%"


def test_unsigned_level_preserves_change_sign():
    from app.generation.generate import _unsigned_levels

    assert _unsigned_levels("Logo Churn % was +1.8%, up +0.3 points.") == "Logo Churn % was 1.8%, up +0.3 points."


def test_written_ratio_retains_precision():
    from openpyxl import Workbook

    from app.close_export import _number, _row

    sheet = Workbook().active
    assert _row(sheet, [_number(12.345678, relative=True)])[0].value == 0.1235
