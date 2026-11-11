"""Pure verification of PayPal order data.

No database and no network access here, so every rule is unit-testable.

Outcomes:

* returns ``VerifiedCapture``      - everything matches, safe to complete.
* raises ``PaymentMismatch``       - PayPal data is well-formed but does NOT
  match what we expect. This is a confirmed problem -> manual review.
* raises ``PaymentPending``        - money has not settled yet (capture is
  PENDING). Not a failure; reconcile again later.
* raises ``PayPalMalformedResponseError`` - containers have the wrong shape.
  Outcome unknown -> recoverable, never reported as a failure.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.paypal_client import PayPalMalformedResponseError

CURRENCY = "USD"
_CAPTURE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PaymentMismatch(Exception):
    """Confirmed discrepancy between PayPal's data and our expectations."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


class PaymentPending(Exception):
    """PayPal has accepted the payment but it has not settled yet."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExpectedPayment:
    order_id: str
    plan_id: str
    amount: Decimal
    currency: str = CURRENCY


@dataclass(frozen=True)
class VerifiedCapture:
    capture_id: str
    amount: Decimal
    currency: str


def _money(node: object, label: str) -> Decimal:
    """Parse {"currency_code": "USD", "value": "24.99"} strictly."""
    if not isinstance(node, dict):
        raise PaymentMismatch(f"{label}_missing")

    if node.get("currency_code") != CURRENCY:
        raise PaymentMismatch("currency_mismatch")

    value = node.get("value")
    if not isinstance(value, str):
        raise PaymentMismatch("amount_invalid")

    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise PaymentMismatch("amount_invalid") from None

    if not amount.is_finite():
        raise PaymentMismatch("amount_invalid")

    return amount


def _single_unit(order: dict) -> dict:
    units = order.get("purchase_units")

    if not isinstance(units, list):
        raise PayPalMalformedResponseError("PayPal purchase units are malformed.")

    if len(units) != 1:
        raise PaymentMismatch("purchase_unit_count")

    unit = units[0]
    if not isinstance(unit, dict):
        raise PayPalMalformedResponseError("PayPal purchase unit is malformed.")

    return unit


def _check_unit_identity(unit: dict, expected: ExpectedPayment) -> None:
    if unit.get("reference_id") != expected.plan_id:
        raise PaymentMismatch("reference_id_mismatch")

    #if unit.get("custom_id") != expected.plan_id:
    #    raise PaymentMismatch("custom_id_mismatch")


def verify_order_before_capture(
    order: object,
    expected: ExpectedPayment,
) -> None:
    """Sanity-check an APPROVED order BEFORE asking PayPal to capture it.

    Purchase-unit details are checked only when PayPal includes them; the
    strict check runs again on the capture response.
    """
    if not isinstance(order, dict):
        raise PayPalMalformedResponseError("PayPal order is malformed.")

    if order.get("id") != expected.order_id:
        raise PaymentMismatch("order_id_mismatch")

    if "purchase_units" not in order:
        return

    unit = _single_unit(order)
    _check_unit_identity(unit, expected)

    if "amount" in unit and _money(unit["amount"], "order_amount") != expected.amount:
        raise PaymentMismatch("amount_mismatch")


def verify_completed_order(
    order: object,
    expected: ExpectedPayment,
) -> VerifiedCapture:
    """Strictly verify a COMPLETED order and return its single capture."""
    if not isinstance(order, dict):
        raise PayPalMalformedResponseError("PayPal order is malformed.")

    if order.get("id") != expected.order_id:
        raise PaymentMismatch("order_id_mismatch")

    if order.get("status") != "COMPLETED":
        raise PaymentPending("order_not_completed")

    unit = _single_unit(order)
    _check_unit_identity(unit, expected)

    if "amount" in unit and _money(unit["amount"], "order_amount") != expected.amount:
        raise PaymentMismatch("amount_mismatch")

    payments = unit.get("payments", {})
    if not isinstance(payments, dict):
        raise PayPalMalformedResponseError("PayPal payments data is malformed.")

    captures = payments.get("captures", [])
    if not isinstance(captures, list) or not all(
        isinstance(item, dict) for item in captures
    ):
        raise PayPalMalformedResponseError("PayPal capture data is malformed.")

    completed = [c for c in captures if c.get("status") == "COMPLETED"]

    if len(completed) > 1:
        raise PaymentMismatch("multiple_completed_captures")

    if not completed:
        if any(c.get("status") == "PENDING" for c in captures):
            raise PaymentPending("capture_pending")
        raise PaymentMismatch("no_completed_capture")

    capture = completed[0]
    capture_id = capture.get("id")

    if not isinstance(capture_id, str) or not _CAPTURE_ID_RE.match(capture_id):
        raise PaymentMismatch("missing_capture_id")

    amount = _money(capture.get("amount"), "capture_amount")

    if amount != expected.amount:
        raise PaymentMismatch("amount_mismatch")

    return VerifiedCapture(
        capture_id=capture_id,
        amount=amount,
        currency=CURRENCY,
    )
