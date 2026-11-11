"""Supported dataset currencies; metadata only, with no conversion."""

from typing import Literal, get_args

Currency = Literal["USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD", "SGD", "CHF"]
CURRENCIES = get_args(Currency)


def validate_currency(value: object) -> str:
    if not isinstance(value, str) or value not in CURRENCIES:
        raise ValueError(f"currency must be one of {', '.join(CURRENCIES)}")
    return value
