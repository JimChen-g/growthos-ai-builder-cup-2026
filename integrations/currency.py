"""Currency boundaries shared by read-only business connectors."""

from backend.currency import normalize_currency_code
from integrations.base import IntegrationError


class AccountCurrencyMismatchError(IntegrationError):
    """Safe failure when reporting configuration would mislabel account amounts."""

    def __init__(self) -> None:
        super().__init__(
            "The account currency does not match the saved reporting currency. "
            "Update the connection currency and test again."
        )


def verify_account_currency(actual: object, expected: str) -> str:
    if not isinstance(actual, str):
        raise IntegrationError("The provider did not return its account currency.")
    try:
        normalized = normalize_currency_code(actual)
    except ValueError:
        raise IntegrationError("The provider returned an invalid account currency.") from None
    if normalized != normalize_currency_code(expected):
        raise AccountCurrencyMismatchError()
    return normalized
