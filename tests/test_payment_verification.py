"""Pure verification rules (no database, no network)."""

from decimal import Decimal

import pytest

from app.payment_verification import (
    ExpectedPayment,
    PaymentMismatch,
    PaymentPending,
    verify_completed_order,
    verify_order_before_capture,
)
from app.paypal_client import PayPalMalformedResponseError
from tests.helpers import approved_order, paypal_order

PLAN = "plan-1"
ORDER = "ORDER-1"
EXPECTED = ExpectedPayment(order_id=ORDER, plan_id=PLAN, amount=Decimal("24.99"))


def good(**overrides):
    return paypal_order(ORDER, PLAN, capture_id="CAP-1", **overrides)


def test_valid_completed_order_is_accepted():
    verified = verify_completed_order(good(), EXPECTED)

    assert verified.capture_id == "CAP-1"
    assert verified.amount == Decimal("24.99")
    assert verified.currency == "USD"


def test_equivalent_decimal_formatting_is_accepted():
    # "24.990" is the same money as "24.99".
    assert verify_completed_order(good(amount="24.990"), EXPECTED)


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"amount": "19.99"}, "amount_mismatch"),
        ({"amount": "24.98"}, "amount_mismatch"),
        ({"amount": "not-a-number"}, "amount_invalid"),
        ({"amount": "NaN"}, "amount_invalid"),
        ({"currency": "EUR"}, "currency_mismatch"),
        ({"reference_id": "someone-elses-plan"}, "reference_id_mismatch"),
        ({"custom_id": "someone-elses-plan"}, "custom_id_mismatch"),
        ({"capture_status": "DECLINED"}, "no_completed_capture"),
        ({"capture_status": "REFUNDED"}, "no_completed_capture"),
        ({"captures": []}, "no_completed_capture"),
    ],
)
def test_confirmed_mismatches_are_rejected(overrides, code):
    order = good(**overrides)

    with pytest.raises(PaymentMismatch) as excinfo:
        verify_completed_order(order, EXPECTED)

    assert excinfo.value.code == code


def test_wrong_order_id_is_rejected():
    order = good()
    order["id"] = "SOME-OTHER-ORDER"

    with pytest.raises(PaymentMismatch) as excinfo:
        verify_completed_order(order, EXPECTED)

    assert excinfo.value.code == "order_id_mismatch"


def test_missing_capture_id_is_rejected():
    order = paypal_order(ORDER, PLAN, capture_id=None)

    with pytest.raises(PaymentMismatch) as excinfo:
        verify_completed_order(order, EXPECTED)

    assert excinfo.value.code == "missing_capture_id"


def test_capture_id_with_unsafe_characters_is_rejected():
    order = paypal_order(ORDER, PLAN, capture_id="../etc/passwd")

    with pytest.raises(PaymentMismatch):
        verify_completed_order(order, EXPECTED)


def test_multiple_completed_captures_are_rejected():
    captures = [
        {"id": "CAP-1", "status": "COMPLETED",
         "amount": {"currency_code": "USD", "value": "24.99"}},
        {"id": "CAP-2", "status": "COMPLETED",
         "amount": {"currency_code": "USD", "value": "24.99"}},
    ]

    with pytest.raises(PaymentMismatch) as excinfo:
        verify_completed_order(good(captures=captures), EXPECTED)

    assert excinfo.value.code == "multiple_completed_captures"


def test_wrong_number_of_purchase_units_is_rejected():
    order = good()
    order["purchase_units"].append(dict(order["purchase_units"][0]))

    with pytest.raises(PaymentMismatch) as excinfo:
        verify_completed_order(order, EXPECTED)

    assert excinfo.value.code == "purchase_unit_count"


def test_order_level_amount_mismatch_is_rejected():
    order = good()
    order["purchase_units"][0]["amount"]["value"] = "99.00"

    with pytest.raises(PaymentMismatch) as excinfo:
        verify_completed_order(order, EXPECTED)

    assert excinfo.value.code == "amount_mismatch"


def test_pending_capture_is_not_a_failure():
    order = paypal_order(ORDER, PLAN, capture_id="CAP-1", capture_status="PENDING")

    with pytest.raises(PaymentPending) as excinfo:
        verify_completed_order(order, EXPECTED)

    assert excinfo.value.code == "capture_pending"


def test_non_completed_order_is_pending_not_failed():
    with pytest.raises(PaymentPending):
        verify_completed_order(good(status="PAYER_ACTION_REQUIRED"), EXPECTED)


@pytest.mark.parametrize(
    "broken",
    [
        "not a dict",
        None,
        [],
    ],
)
def test_non_object_response_is_malformed_not_a_mismatch(broken):
    with pytest.raises(PayPalMalformedResponseError):
        verify_completed_order(broken, EXPECTED)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.update(purchase_units="oops"),
        lambda o: o["purchase_units"].__setitem__(0, "oops"),
        lambda o: o["purchase_units"][0].update(payments="oops"),
        lambda o: o["purchase_units"][0]["payments"].update(captures="oops"),
        lambda o: o["purchase_units"][0]["payments"].update(captures=["oops"]),
    ],
)
def test_wrongly_shaped_containers_are_malformed(mutate):
    order = good()
    mutate(order)

    with pytest.raises(PayPalMalformedResponseError):
        verify_completed_order(order, EXPECTED)


def test_pre_capture_accepts_minimal_and_full_approved_orders():
    verify_order_before_capture({"id": ORDER, "status": "APPROVED"}, EXPECTED)
    verify_order_before_capture(approved_order(ORDER, PLAN), EXPECTED)


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda o: o.update(id="OTHER"), "order_id_mismatch"),
        (lambda o: o["purchase_units"][0].update(reference_id="x"), "reference_id_mismatch"),
        (lambda o: o["purchase_units"][0].update(custom_id="x"), "custom_id_mismatch"),
        (lambda o: o["purchase_units"][0]["amount"].update(value="1.00"), "amount_mismatch"),
        (lambda o: o["purchase_units"][0]["amount"].update(currency_code="EUR"), "currency_mismatch"),
    ],
)
def test_pre_capture_rejects_a_wrong_order_before_any_money_moves(mutate, code):
    order = approved_order(ORDER, PLAN)
    mutate(order)

    with pytest.raises(PaymentMismatch) as excinfo:
        verify_order_before_capture(order, EXPECTED)

    assert excinfo.value.code == code
