"""Supported dataset currencies; metadata only, with no conversion."""

from typing import Literal, get_args

Currency = Literal["USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD", "SGD", "CHF"]
CURRENCIES = get_args(Currency)


# How each currency is written before an amount. The figure check reads $ € £ ₹ ¥
# as currency marks; CA$/A$/S$ end in "$" and CHF is written as a word.
SYMBOLS = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "INR": "\u20b9", "JPY": "\u00a5",
           "CAD": "CA$", "AUD": "A$", "SGD": "S$", "CHF": "CHF\u00a0"}


def symbol(code: str | None) -> str:
    """The mark written before an amount in this currency ($ when unknown)."""
    return SYMBOLS.get(code or "USD", "$")


def validate_currency(value: object) -> str:
    if not isinstance(value, str) or value not in CURRENCIES:
        raise ValueError(f"currency must be one of {', '.join(CURRENCIES)}")
    return value
