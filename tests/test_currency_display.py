"""P6: amounts are written in the dataset's currency wherever they're formatted:
the model's input (so commentary copies the right symbol), the board pack, the
figure-check messages, and the screen. Nothing is converted."""

import subprocess
from pathlib import Path

import pytest

from app.board_pack import _fmt as pack_fmt
from app.generation.guard import check_faithfulness, faithfulness_messages
from app.generation.prompts import _fmt as prompt_fmt
from app.generation.prompts import build_user_prompt
from app.schemas import ComputedFact


def _fact(currency):
    return ComputedFact(metric="Net Revenue", category="Revenue", period="2025-03", value=4200000,
                        prior_value=4772727, unit="USD", currency=currency,
                        deltas={"mom_pct": -12.0, "yoy_pct": 4.5, "budget_var_abs": -370000, "budget_var_pct": -8.1},
                        trend="up", is_anomaly=False)


@pytest.mark.parametrize("currency,want", [("USD", "$4.20M"), ("EUR", "€4.20M"), ("INR", "₹4.20M"),
                                           ("GBP", "£4.20M"), ("CAD", "CA$4.20M")])
def test_model_input_uses_the_dataset_currency(currency, want):
    prompt = build_user_prompt(_fact(currency), [])
    assert f"value: {want}" in prompt
    assert "budget_variance_absolute: −" + want[: -5] in prompt  # same mark on the variance


def test_usd_model_input_is_unchanged():
    assert prompt_fmt(-370000, "USD") == "−$370K"
    assert prompt_fmt(12.5, "USD") == "$12.50"
    assert prompt_fmt(1234, "USD", "JPY") == "¥1K" and prompt_fmt(950, "USD", "JPY") == "¥950"


def test_board_pack_amounts_use_the_currency():
    assert pack_fmt(4200000, "USD") == "$4.20M"
    assert pack_fmt(4200000, "USD", "EUR") == "€4.20M"
    assert pack_fmt(-370000, "USD", "INR") == "-₹370K"
    assert pack_fmt(None, "USD", "EUR") == "–" and pack_fmt(12.0, "%", "EUR") == "12%"


def test_euro_commentary_passes_and_messages_quote_euros():
    fact = _fact("EUR")
    assert check_faithfulness("Net Revenue fell 12% to €4.20M, €370K below plan.", fact)[0]
    msgs = faithfulness_messages("Net Revenue was €4.90M.", fact)
    assert msgs and any("€" in m for m in msgs) and not any("$" in m for m in msgs), msgs


def _js_fmt(expr):
    source = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("// unit \"USD\" means \"an amount of money\"")
    end = source.index("const pct=", start)
    script = source[start:end] + f"\nconsole.log({expr});"
    try:
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                             check=True, timeout=20)
    except FileNotFoundError:
        pytest.skip("node is required")
    return out.stdout.strip()


def test_screen_formats_amounts_in_the_active_currency():
    assert _js_fmt('fmt(-370000,"USD")') == "−$370K"
    assert _js_fmt('(displayCurrency="INR",fmt(4200000,"USD"))') == "₹4.20M"
    assert _js_fmt('(displayCurrency="EUR",fmt(12.5,"USD"))') == "€12.5"
    # AI spend is billed in dollars whatever the dataset's currency.
    assert _js_fmt('(displayCurrency="EUR",fmt(0.42,"USD","USD"))') == "$0.42"
    assert _js_fmt('(displayCurrency="JPY",fmt(950.4,"USD"))') == "¥950"
    assert _js_fmt('(displayCurrency="EUR",fmt(5,"%"))') == "5.0%"


def test_screen_and_server_agree_on_currency_marks():
    from app.currency import CURRENCIES, SYMBOLS
    assert set(SYMBOLS) == set(CURRENCIES)
    for code, mark in SYMBOLS.items():
        assert _js_fmt(f'(displayCurrency="{code}",fmt(4200000,"USD"))') == mark + "4.20M", code
