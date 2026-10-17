"""Currency validation without importing any provider adapter or credential code."""

import re

# Explicitly supported Stripe charge/price units. New currencies must be checked
# against Stripe's presentment/minor-unit rules before extending this set.
STRIPE_SUPPORTED_CURRENCIES = frozenset(
    "USD EUR GBP CNY HKD TWD JPY KRW SGD AUD CAD NZD CHF INR IDR MYR THB VND PHP "
    "BRL MXN AED SAR ZAR DKK NOK SEK PLN CZK HUF ISK UGX BIF CLP DJF GNF KMF "
    "MGA PYG RWF VUV XAF XOF XPF".split()
)


def normalize_currency_code(value: str) -> str:
    """Normalize the ISO-style code, never interpret a display symbol as a currency."""
    normalized = value.strip().upper()
    if re.fullmatch(r"[A-Z]{3}", normalized) is None:
        raise ValueError("currency must be a three-letter currency code")
    return normalized


def normalize_stripe_currency(value: str) -> str:
    normalized = normalize_currency_code(value)
    if normalized not in STRIPE_SUPPORTED_CURRENCIES:
        raise ValueError("this Stripe reporting currency is not supported")
    return normalized
