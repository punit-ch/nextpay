"""Payment lifecycle service tests.

Real SQLite (temporary file), fake PayPal. No FastAPI needed.
"""

import asyncio
import copy
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import update

from app import database, payments
from app.models import Payment, PaymentEvent, PurchasePlan
from app.paypal_client import (
    PayPalAuthError,
    PayPalMalformedResponseError,
    PayPalRejectedError,
    PayPalUnavailableError,
)
from tests.helpers import (
    approved_order,
    make_plan_and_payment,
    paypal_order,
    read_state,
)


# --------------------------------------------------------------------------
# Test doubles and helpers
# --------------------------------------------------------------------------


class FakePayPal:
    """Stands in for PayPal's authoritative order state."""

    def __init__(self, plan_id, order_id, amount="24.99"):
        self.plan_id = plan_id
        self.order_id = order_id
        self.amount = amount
        self.capture_id = f"TEST-CAPTURE-{uuid4()}"
        self.server_status = "APPROVED"  # what PayPal currently believes
        self.completed_overrides = {}
        self.lookup_calls = 0
        self.capture_calls = 0
        self.lookup_error = None
        self.capture_error = None
        self.capture_lands_before_error = False
        self.lookup_delay = 0
        self.order_override = None

    def _completed(self):
        params = {"amount": self.amount, **self.completed_overrides}
        return paypal_order(
            self.order_id,
            self.plan_id,
            capture_id=self.capture_id,
            **params,
        )

    def _current(self):
        if self.order_override is not None:
            return copy.deepcopy(self.order_override)
        if self.server_status == "COMPLETED":
            return self._completed()
        return approved_order(self.order_id, self.plan_id, self.amount)

    async def get_order(self, order_id):
        self.lookup_calls += 1
        if self.lookup_delay:
            await asyncio.sleep(self.lookup_delay)
        if self.lookup_error:
            raise self.lookup_error
        return self._current()

    async def capture_order(self, order_id):
        self.capture_calls += 1
        if self.capture_error:
            if self.capture_lands_before_error:
                self.server_status = "COMPLETED"  # PayPal charged; reply lost
            raise self.capture_error
        self.server_status = "COMPLETED"
        return self._completed()


def setup_payment(**kwargs):
    plan_id, order_id = make_plan_and_payment(**kwargs)
    return plan_id, order_id, FakePayPal(plan_id, order_id)


def run_return(fake, order_id=None):
    return asyncio.run(
        payments.process_paypal_return(
            fake.order_id if order_id is None else order_id,
            get_order=fake.get_order,
            capture_order=fake.capture_order,
        )
    )


def cancel(plan_id, token=None, fake=None):
    return asyncio.run(
        payments.cancel_checkout(
            plan_id, token, get_order=fake.get_order if fake else None
        )
    )


def make_stale(plan_id, seconds=600):
    old = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    with database.SessionLocal() as db:
        db.execute(update(Payment).where(Payment.plan_id == plan_id).values(updated_at=old))
        db.commit()


def assert_consistent(plan_id):
    """Plan and ledger must always carry the same payment state."""
    plan_status, payment_status, plan_capture, payment_capture = read_state(plan_id)
    assert plan_status == payment_status
    assert plan_capture == payment_capture


def event_types(plan_id):
    with database.SessionLocal() as db:
        rows = db.query(PaymentEvent).filter(PaymentEvent.plan_id == plan_id).order_by(PaymentEvent.event_id).all()
        return [row.event_type for row in rows]


# --------------------------------------------------------------------------
# Successful capture and idempotency
# --------------------------------------------------------------------------


def test_successful_capture_completes_plan_and_ledger_together():
    plan_id, order_id, fake = setup_payment()

    outcome = run_return(fake)

    assert outcome.result == "success"
    assert outcome.plan_id == plan_id
    assert read_state(plan_id) == ("completed", "completed", fake.capture_id, fake.capture_id)
    assert fake.capture_calls == 1
    assert event_types(plan_id) == ["capture_claimed", "capture_completed"]


def test_duplicate_callback_after_success_does_not_touch_paypal_again():
    plan_id, order_id, fake = setup_payment()
    run_return(fake)
    lookups, captures = fake.lookup_calls, fake.capture_calls

    for _ in range(3):
        assert run_return(fake).result == "success"

    assert (fake.lookup_calls, fake.capture_calls) == (lookups, captures)
    assert read_state(plan_id)[2] == fake.capture_id


def test_order_already_completed_at_paypal_is_recorded_without_capturing():
    plan_id, order_id, fake = setup_payment()
    fake.server_status = "COMPLETED"  # e.g. an earlier attempt landed

    outcome = run_return(fake)

    assert outcome.result == "success"
    assert fake.capture_calls == 0
    assert read_state(plan_id)[:2] == ("completed", "completed")


# --------------------------------------------------------------------------
# Confirmed mismatches -> review_required
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"amount": "19.99"}, "amount_mismatch"),
        ({"currency": "EUR"}, "currency_mismatch"),
        ({"reference_id": "other-plan"}, "reference_id_mismatch"),
        ({"custom_id": "other-plan"}, "custom_id_mismatch"),
        ({"capture_status": "DECLINED"}, "no_completed_capture"),
        ({"captures": []}, "no_completed_capture"),
    ],
)
def test_confirmed_capture_mismatch_requires_review(overrides, code):
    plan_id, order_id, fake = setup_payment()
    fake.completed_overrides = overrides

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == code
    assert read_state(plan_id) == ("review_required", "review_required", None, None)


def test_wrong_order_id_in_capture_response_requires_review():
    plan_id, order_id, fake = setup_payment()
    original = fake._completed

    def wrong_id():
        order = original()
        order["id"] = "SOME-OTHER-ORDER"
        return order

    fake._completed = wrong_id

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == "order_id_mismatch"
    assert_consistent(plan_id)


def test_missing_capture_id_requires_review():
    plan_id, order_id, fake = setup_payment()
    fake.capture_id = None

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == "missing_capture_id"
    assert read_state(plan_id)[2] is None


def test_multiple_completed_captures_require_review():
    plan_id, order_id, fake = setup_payment()
    cap = {"status": "COMPLETED", "amount": {"currency_code": "USD", "value": "24.99"}}
    fake.completed_overrides = {"captures": [dict(cap, id="C-1"), dict(cap, id="C-2")]}

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == "multiple_completed_captures"


def test_wrong_order_at_paypal_is_flagged_before_any_capture():
    plan_id, order_id, fake = setup_payment()
    fake.order_override = approved_order(order_id, plan_id, amount="1.00")

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == "amount_mismatch"
    assert fake.capture_calls == 0  # no money moved
    assert read_state(plan_id)[:2] == ("review_required", "review_required")


def test_lookup_returning_a_different_order_requires_review_without_capture():
    plan_id, order_id, fake = setup_payment()
    fake.order_override = approved_order("SOMEONE-ELSES-ORDER", plan_id)

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == "order_id_mismatch"
    assert fake.capture_calls == 0


def test_capture_id_already_used_by_another_payment_requires_review():
    other_plan, _, other_fake = setup_payment()
    run_return(other_fake)

    plan_id, order_id, fake = setup_payment()
    fake.capture_id = other_fake.capture_id  # collides with the first payment

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == "capture_id_conflict"
    assert read_state(plan_id) == ("review_required", "review_required", None, None)
    assert read_state(other_plan)[0] == "completed"  # untouched


# --------------------------------------------------------------------------
# Local ledger consistency (checked BEFORE contacting PayPal)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"ledger_amount": "10.00"}, "ledger_amount_mismatch"),
        ({"ledger_currency": "EUR"}, "ledger_currency_mismatch"),
        ({"unit_price": "5.00"}, "plan_total_mismatch"),
        ({"budget_limit": "10.00"}, "budget_exceeded"),
    ],
)
def test_inconsistent_local_ledger_blocks_capture(kwargs, code):
    plan_id, order_id, fake = setup_payment(**kwargs)

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == code
    assert (fake.lookup_calls, fake.capture_calls) == (0, 0)
    assert_consistent(plan_id)


def test_ledger_row_pointing_at_another_plan_is_not_captured():
    plan_id, order_id, fake = setup_payment()
    stranger, _ = make_plan_and_payment()
    with database.SessionLocal() as db:
        # Detach the ledger row from its plan (simulated corruption).
        db.execute(update(PurchasePlan).where(PurchasePlan.plan_id == plan_id).values(paypal_order_id=None))
        db.commit()

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == "ownership_mismatch"
    assert fake.capture_calls == 0


def test_plan_and_ledger_disagreeing_is_flagged_not_captured():
    plan_id, order_id, fake = setup_payment()
    with database.SessionLocal() as db:
        db.execute(update(Payment).where(Payment.plan_id == plan_id).values(status="capturing"))
        db.commit()

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == "status_mismatch"
    assert fake.capture_calls == 0


# --------------------------------------------------------------------------
# Uncertain outcomes must stay recoverable (never "failed")
# --------------------------------------------------------------------------


def test_timeout_during_capture_keeps_payment_recoverable():
    plan_id, order_id, fake = setup_payment()
    fake.capture_error = PayPalUnavailableError("PayPal timed out while capturing the payment.")

    outcome = run_return(fake)

    assert outcome.result == "pending"
    assert outcome.reason == "paypal_unavailable"
    assert read_state(plan_id) == ("capturing", "capturing", None, None)
    assert "reconcile_pending" in event_types(plan_id)


def test_interrupted_capture_is_reconciled_without_a_second_charge():
    """PayPal charged the buyer but the reply was lost (connection dropped)."""
    plan_id, order_id, fake = setup_payment()
    fake.capture_error = PayPalUnavailableError("connection dropped")
    fake.capture_lands_before_error = True

    first = run_return(fake)
    assert first.result == "pending"
    assert read_state(plan_id)[:2] == ("capturing", "capturing")

    # A refresh straight away is told it is still being handled...
    assert run_return(fake).result == "processing"

    # ...and once the claim is stale, reconciliation uses PayPal's state.
    make_stale(plan_id)
    fake.capture_error = None
    recovered = run_return(fake)

    assert recovered.result == "success"
    assert fake.capture_calls == 1  # never captured a second time
    assert read_state(plan_id) == ("completed", "completed", fake.capture_id, fake.capture_id)
    assert "capture_reclaimed" in event_types(plan_id)


def test_crash_after_capture_but_before_commit_is_recovered(monkeypatch):
    plan_id, order_id, fake = setup_payment()
    real_apply = payments.apply_transition
    failures = {"left": 1}

    def flaky_apply(plan, order, from_status, to_status, **kwargs):
        if to_status == payments.COMPLETED and failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("simulated crash before commit")
        return real_apply(plan, order, from_status, to_status, **kwargs)

    monkeypatch.setattr(payments, "apply_transition", flaky_apply)

    first = run_return(fake)

    assert first.result == "pending"
    assert read_state(plan_id)[:2] == ("capturing", "capturing")
    assert fake.capture_calls == 1

    make_stale(plan_id)
    second = run_return(fake)

    assert second.result == "success"
    assert fake.capture_calls == 1  # PayPal already had it: no re-capture
    assert read_state(plan_id) == ("completed", "completed", fake.capture_id, fake.capture_id)


@pytest.mark.parametrize(
    "error, code",
    [
        (PayPalUnavailableError("down"), "paypal_unavailable"),
        (PayPalAuthError("bad credentials"), "paypal_configuration"),
        (PayPalMalformedResponseError("garbage"), "malformed_response"),
        (PayPalRejectedError("rejected", status_code=400, issue="SOMETHING_ELSE"), "paypal_rejected"),
    ],
)
def test_order_lookup_failure_never_changes_payment_outcome(error, code):
    plan_id, order_id, fake = setup_payment()
    fake.lookup_error = error

    outcome = run_return(fake)

    assert outcome.result == "pending"
    assert outcome.reason == code
    assert fake.capture_calls == 0
    # Nothing was claimed: the payment is exactly where it was.
    assert read_state(plan_id) == (
        "awaiting_paypal_approval", "awaiting_paypal_approval", None, None
    )

    # ...so an immediate retry works without waiting for any lease.
    fake.lookup_error = None
    assert run_return(fake).result == "success"
    assert_consistent(plan_id)


def test_lookup_failure_then_recovery_completes_the_payment():
    plan_id, order_id, fake = setup_payment()
    fake.lookup_error = PayPalUnavailableError("down")
    assert run_return(fake).result == "pending"

    make_stale(plan_id)
    fake.lookup_error = None

    assert run_return(fake).result == "success"
    assert_consistent(plan_id)


@pytest.mark.parametrize(
    "error, code",
    [
        (PayPalAuthError("expired token"), "paypal_configuration"),
        (PayPalMalformedResponseError("garbage"), "malformed_response"),
        (PayPalRejectedError("nope", status_code=422, issue="TRANSACTION_REFUSED"), "paypal_rejected"),
    ],
)
def test_other_capture_errors_stay_recoverable(error, code):
    plan_id, order_id, fake = setup_payment()
    fake.capture_error = error

    outcome = run_return(fake)

    assert outcome.result == "pending"
    assert outcome.reason == code
    assert read_state(plan_id)[:2] == ("capturing", "capturing")


def test_unexpected_exception_is_logged_and_not_reported_as_failure(caplog):
    plan_id, order_id, fake = setup_payment()
    fake.capture_error = ZeroDivisionError("bug")

    with caplog.at_level("ERROR", logger="nextpay.payments"):
        outcome = run_return(fake)

    assert outcome.result == "pending"
    assert outcome.reason == "internal_error"
    assert "Unexpected error" in caplog.text  # not silently swallowed
    assert read_state(plan_id)[:2] == ("capturing", "capturing")


def test_malformed_capture_response_stays_recoverable():
    plan_id, order_id, fake = setup_payment()
    fake.completed_overrides = {"captures": "not-a-list"}

    outcome = run_return(fake)

    assert outcome.result == "pending"
    assert outcome.reason == "malformed_response"
    assert read_state(plan_id)[:2] == ("capturing", "capturing")


def test_pending_capture_is_not_treated_as_failure_or_success():
    plan_id, order_id, fake = setup_payment()
    fake.completed_overrides = {"capture_status": "PENDING"}

    outcome = run_return(fake)

    assert outcome.result == "pending"
    assert outcome.reason == "capture_pending"
    assert read_state(plan_id) == ("capturing", "capturing", None, None)


def test_unknown_order_status_at_paypal_stays_recoverable():
    plan_id, order_id, fake = setup_payment()
    fake.order_override = {"id": order_id, "status": "SOMETHING_NEW"}

    assert run_return(fake).result == "pending"
    assert fake.capture_calls == 0
    assert read_state(plan_id)[:2] == (
        "awaiting_paypal_approval", "awaiting_paypal_approval"
    )


# --------------------------------------------------------------------------
# Definitive PayPal answers
# --------------------------------------------------------------------------


def test_declined_instrument_marks_payment_failed():
    plan_id, order_id, fake = setup_payment()
    fake.capture_error = PayPalRejectedError("declined", status_code=422, issue="INSTRUMENT_DECLINED")

    outcome = run_return(fake)

    assert outcome.result == "failed"
    assert read_state(plan_id) == ("failed", "failed", None, None)


def test_voided_order_marks_payment_failed():
    plan_id, order_id, fake = setup_payment()
    fake.order_override = {"id": order_id, "status": "VOIDED"}

    assert run_return(fake).result == "failed"
    assert fake.capture_calls == 0


def test_order_already_captured_error_is_resolved_by_lookup():
    plan_id, order_id, fake = setup_payment()
    fake.capture_error = PayPalRejectedError("already", status_code=422, issue="ORDER_ALREADY_CAPTURED")
    fake.capture_lands_before_error = True

    assert run_return(fake).result == "success"
    assert fake.capture_calls == 1


# --------------------------------------------------------------------------
# Capturing unapproved orders / invalid states
# --------------------------------------------------------------------------


@pytest.mark.parametrize("paypal_status", ["CREATED", "SAVED", "PAYER_ACTION_REQUIRED"])
def test_unapproved_order_is_never_captured(paypal_status):
    plan_id, order_id, fake = setup_payment()
    fake.order_override = {"id": order_id, "status": paypal_status}

    outcome = run_return(fake)

    assert outcome.result == "not_approved"
    assert fake.capture_calls == 0
    # The claim is released so the buyer can still approve and come back.
    assert read_state(plan_id)[:2] == ("awaiting_paypal_approval", "awaiting_paypal_approval")


def test_unknown_order_token_changes_nothing():
    plan_id, order_id, fake = setup_payment()

    outcome = run_return(fake, order_id="NO-SUCH-ORDER")

    assert outcome.result == "not_found"
    assert (fake.lookup_calls, fake.capture_calls) == (0, 0)
    assert read_state(plan_id)[:2] == ("awaiting_paypal_approval", "awaiting_paypal_approval")


@pytest.mark.parametrize("token", ["", "a/b", "../x", "x?y=1", "a" * 300])
def test_malformed_tokens_are_rejected_before_any_lookup(token):
    plan_id, order_id, fake = setup_payment()

    assert run_return(fake, order_id=token).result == "not_found"
    assert (fake.lookup_calls, fake.capture_calls) == (0, 0)


@pytest.mark.parametrize(
    "status, result",
    [
        ("cancelled_by_buyer", "cancelled"),
        ("review_required", "review_required"),
        ("failed", "failed"),
        ("completed", "success"),
        ("some_invented_state", "invalid_state"),
        ("not_started", "invalid_state"),
    ],
)
def test_terminal_and_unexpected_states_never_reach_paypal(status, result):
    plan_id, order_id, fake = setup_payment(payment_status=status)

    outcome = run_return(fake)

    assert outcome.result == result
    assert (fake.lookup_calls, fake.capture_calls) == (0, 0)
    assert read_state(plan_id)[:2] == (status, status)


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------


def test_simultaneous_callbacks_capture_exactly_once():
    plan_id, order_id, fake = setup_payment()
    fake.lookup_delay = 0.05

    async def both():
        return await asyncio.gather(
            *[
                payments.process_paypal_return(
                    order_id, get_order=fake.get_order, capture_order=fake.capture_order
                )
                for _ in range(5)
            ]
        )

    results = asyncio.run(both())

    assert fake.capture_calls == 1  # the invariant: exactly one capture
    assert all(r.result in ("success", "processing") for r in results)
    assert any(r.result == "success" for r in results)
    assert read_state(plan_id) == ("completed", "completed", fake.capture_id, fake.capture_id)


def test_callbacks_from_separate_threads_capture_exactly_once():
    plan_id, order_id, fake = setup_payment()
    fake.lookup_delay = 0.05
    barrier = threading.Barrier(6)
    results, errors = [], []

    def worker():
        try:
            barrier.wait(timeout=10)
            results.append(run_return(fake))
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    assert len(results) == 6
    assert fake.capture_calls == 1
    assert sum(1 for r in results if r.result == "success") >= 1
    assert all(r.result in ("success", "processing") for r in results)
    assert read_state(plan_id) == ("completed", "completed", fake.capture_id, fake.capture_id)


def test_active_claim_blocks_other_callbacks_without_contacting_paypal():
    plan_id, order_id, fake = setup_payment(payment_status="capturing")

    outcome = run_return(fake)

    assert outcome.result == "processing"
    assert outcome.reason == "capture_in_progress"
    assert (fake.lookup_calls, fake.capture_calls) == (0, 0)


def test_only_one_caller_can_reclaim_a_stale_claim():
    plan_id, order_id, fake = setup_payment(payment_status="capturing")
    make_stale(plan_id)
    snapshot = payments.load_snapshot(order_id)

    assert payments._reclaim_stale_capture(snapshot) is True
    assert payments._reclaim_stale_capture(snapshot) is False  # snapshot is now outdated


def test_claim_is_atomic_for_plan_and_ledger():
    plan_id, order_id, fake = setup_payment()

    assert payments.apply_transition(
        plan_id, order_id, payments.AWAITING, payments.CAPTURING, event_type="capture_claimed"
    )
    assert not payments.apply_transition(
        plan_id, order_id, payments.AWAITING, payments.CAPTURING, event_type="capture_claimed"
    )
    assert_consistent(plan_id)


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------


def test_terminal_states_have_no_exits():
    assert payments.TERMINAL_STATUSES == {
        payments.COMPLETED,
        payments.CANCELLED,
        payments.REVIEW,
        payments.FAILED,
    }


@pytest.mark.parametrize(
    "source, target",
    [
        (payments.COMPLETED, payments.CAPTURING),
        (payments.COMPLETED, payments.AWAITING),
        (payments.CANCELLED, payments.CAPTURING),
        (payments.REVIEW, payments.COMPLETED),
        (payments.FAILED, payments.COMPLETED),
        (payments.AWAITING, payments.COMPLETED),  # must go through capturing
        (payments.NOT_STARTED, payments.CAPTURING),
        ("invented", payments.COMPLETED),
    ],
)
def test_illegal_transitions_are_refused(source, target):
    plan_id, order_id, _ = setup_payment()

    with database.SessionLocal() as db:
        with pytest.raises(payments.InvalidTransition):
            payments.transition_payment(
                db,
                plan_id=plan_id,
                order_id=order_id,
                from_status=source,
                to_status=target,
                event_type="x",
            )

    assert_consistent(plan_id)


# --------------------------------------------------------------------------
# Buyer cancellation
# --------------------------------------------------------------------------


def test_buyer_cancellation_updates_plan_and_ledger():
    plan_id, order_id, _ = setup_payment()

    outcome = cancel(plan_id, order_id)

    assert outcome.result == "cancelled"
    assert read_state(plan_id) == ("cancelled_by_buyer", "cancelled_by_buyer", None, None)
    assert event_types(plan_id) == ["buyer_cancelled"]


def test_cancel_without_token_still_works_and_is_idempotent():
    plan_id, order_id, _ = setup_payment()

    assert cancel(plan_id).result == "cancelled"
    assert cancel(plan_id).result == "cancelled"
    assert read_state(plan_id)[:2] == ("cancelled_by_buyer", "cancelled_by_buyer")


def test_cancel_with_wrong_order_token_changes_nothing():
    plan_id, order_id, _ = setup_payment()

    outcome = cancel(plan_id, "SOMEONE-ELSES-ORDER")

    assert outcome.result == "invalid_state"
    assert read_state(plan_id)[:2] == ("awaiting_paypal_approval", "awaiting_paypal_approval")


def test_cancel_cannot_undo_a_completed_payment():
    plan_id, order_id, fake = setup_payment()
    run_return(fake)

    outcome = cancel(plan_id, order_id)

    assert outcome.result == "success"  # the buyer is shown the truth
    assert read_state(plan_id)[:2] == ("completed", "completed")


def test_cancel_cannot_interrupt_a_capture_in_progress():
    plan_id, order_id, _ = setup_payment(payment_status="capturing")

    outcome = cancel(plan_id, order_id)

    assert outcome.result == "processing"
    assert read_state(plan_id)[:2] == ("capturing", "capturing")


def test_cancelled_payment_is_not_captured_by_a_late_return():
    plan_id, order_id, fake = setup_payment()
    cancel(plan_id, order_id)

    assert run_return(fake).result == "cancelled"
    assert fake.capture_calls == 0


def test_cancel_for_unknown_plan_reveals_nothing():
    assert cancel("no-such-plan").result == "cancelled"


# --------------------------------------------------------------------------
# Order creation
# --------------------------------------------------------------------------


def make_approved_plan(**overrides):
    values = dict(
        plan_id=str(uuid4()),
        goal="Test",
        product_id="desk_lamp",
        product_name="Adjustable LED Desk Lamp",
        quantity=1,
        unit_price="24.99",
        total="24.99",
        budget_limit="100.00",
        status="approved_by_user",
        payment_status="not_started",
    )
    values.update(overrides)
    with database.SessionLocal() as db:
        db.add(PurchasePlan(**values))
        db.commit()
    return values["plan_id"]


def fake_create(order_id, calls=None, delay=0):
    async def create(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        if delay:
            await asyncio.sleep(delay)
        return {
            "paypal_order_id": order_id,
            "status": "CREATED",
            "approval_url": "https://www.sandbox.paypal.com/checkoutnow?token=" + order_id,
        }

    return create


def test_order_creation_records_ledger_and_uses_server_price():
    plan_id = make_approved_plan()
    order_id = f"TEST-ORDER-{uuid4()}"
    calls = []

    result = asyncio.run(payments.create_order_for_plan(plan_id, create_order=fake_create(order_id, calls)))

    assert result["paypal_order_id"] == order_id
    assert result["payment_status"] == "awaiting_paypal_approval"
    assert calls[0]["amount"] == "24.99" and calls[0]["plan_id"] == plan_id
    with database.SessionLocal() as db:
        payment = db.query(Payment).filter(Payment.plan_id == plan_id).one()
        assert (payment.amount, payment.currency, payment.status) == ("24.99", "USD", "awaiting_paypal_approval")
        assert db.get(PurchasePlan, plan_id).paypal_order_id == order_id
    assert event_types(plan_id) == ["order_created"]


def test_order_creation_ignores_a_tampered_plan_total():
    # Even if the stored total is wrong, PayPal is asked for the catalogue price.
    plan_id = make_approved_plan(total="0.01", unit_price="0.01")
    calls = []

    asyncio.run(payments.create_order_for_plan(plan_id, create_order=fake_create("ORDER-T", calls)))

    assert calls[0]["amount"] == "24.99"


def test_order_creation_requires_user_approval():
    plan_id = make_approved_plan(status="awaiting_user_approval")

    with pytest.raises(payments.ApprovalRequired):
        asyncio.run(payments.create_order_for_plan(plan_id, create_order=fake_create("O")))


@pytest.mark.parametrize("status", ["rejected_by_user", "blocked_by_policy"])
def test_order_creation_refuses_rejected_or_blocked_plans(status):
    plan_id = make_approved_plan(status=status)

    with pytest.raises(payments.ApprovalRequired):
        asyncio.run(payments.create_order_for_plan(plan_id, create_order=fake_create("O")))


def test_order_creation_for_unknown_plan():
    with pytest.raises(payments.PlanNotFound):
        asyncio.run(payments.create_order_for_plan("nope", create_order=fake_create("O")))


def test_second_order_for_the_same_plan_is_refused():
    plan_id = make_approved_plan()
    asyncio.run(payments.create_order_for_plan(plan_id, create_order=fake_create("ORDER-1")))

    with pytest.raises(payments.PaymentAlreadyStarted):
        asyncio.run(payments.create_order_for_plan(plan_id, create_order=fake_create("ORDER-2")))

    with database.SessionLocal() as db:
        assert db.query(Payment).filter(Payment.plan_id == plan_id).count() == 1


def test_policy_is_rechecked_when_the_order_is_created():
    plan_id = make_approved_plan(budget_limit="5.00")  # lamp costs 24.99

    from app.policy import PolicyViolation

    with pytest.raises(PolicyViolation):
        asyncio.run(payments.create_order_for_plan(plan_id, create_order=fake_create("O")))

    assert read_plan_status(plan_id) == "not_started"


def read_plan_status(plan_id):
    with database.SessionLocal() as db:
        return db.get(PurchasePlan, plan_id).payment_status


def test_paypal_failure_during_creation_leaves_the_plan_untouched():
    plan_id = make_approved_plan()

    async def failing(**kwargs):
        raise PayPalUnavailableError("down")

    with pytest.raises(PayPalUnavailableError):
        asyncio.run(payments.create_order_for_plan(plan_id, create_order=failing))

    assert read_plan_status(plan_id) == "not_started"
    with database.SessionLocal() as db:
        assert db.query(Payment).filter(Payment.plan_id == plan_id).count() == 0


def test_double_click_creates_one_ledger_row_and_returns_the_same_order():
    plan_id = make_approved_plan()
    order_id = f"TEST-ORDER-{uuid4()}"

    async def twice():
        create = fake_create(order_id, delay=0.05)  # both requests overlap
        return await asyncio.gather(
            payments.create_order_for_plan(plan_id, create_order=create),
            payments.create_order_for_plan(plan_id, create_order=create),
        )

    first, second = asyncio.run(twice())

    assert first["paypal_order_id"] == second["paypal_order_id"] == order_id
    with database.SessionLocal() as db:
        assert db.query(Payment).filter(Payment.plan_id == plan_id).count() == 1


def test_no_database_session_is_open_while_talking_to_paypal():
    plan_id = make_approved_plan()
    seen = {}

    async def create(**kwargs):
        # A write from another connection succeeds only if no write
        # transaction is being held open by the service.
        with database.SessionLocal() as other:
            other.execute(update(PurchasePlan).where(PurchasePlan.plan_id == plan_id).values(goal="changed"))
            other.commit()
        seen["ok"] = True
        return {"paypal_order_id": "ORDER-X", "status": "CREATED",
                "approval_url": "https://www.sandbox.paypal.com/x"}

    asyncio.run(payments.create_order_for_plan(plan_id, create_order=create))

    assert seen["ok"]


# --------------------------------------------------------------------------
# Manual reconcile and ledger API
# --------------------------------------------------------------------------


def test_reconcile_plan_completes_an_approved_order():
    plan_id, order_id, fake = setup_payment()

    outcome = asyncio.run(
        payments.reconcile_plan(plan_id, get_order=fake.get_order, capture_order=fake.capture_order)
    )

    assert outcome.result == "success"


def test_reconcile_plan_without_an_order_is_invalid_state():
    plan_id = make_approved_plan()
    fake = FakePayPal(plan_id, "x")

    outcome = asyncio.run(
        payments.reconcile_plan(plan_id, get_order=fake.get_order, capture_order=fake.capture_order)
    )

    assert outcome.result == "invalid_state"


def test_reconcile_unknown_plan():
    fake = FakePayPal("p", "o")

    with pytest.raises(payments.PlanNotFound):
        asyncio.run(payments.reconcile_plan("nope", get_order=fake.get_order, capture_order=fake.capture_order))


def test_ledger_view_lists_payments_and_audit_events():
    plan_id, order_id, fake = setup_payment()
    run_return(fake)

    ledger = payments.get_payment_ledger(plan_id)

    assert ledger["payment_status"] == "completed"
    assert ledger["payments"][0]["paypal_capture_id"] == fake.capture_id
    assert [e["event_type"] for e in ledger["events"]] == ["capture_claimed", "capture_completed"]
    assert payments.get_payment_ledger("missing") is None


def test_audit_events_never_contain_paypal_payloads_or_secrets():
    plan_id, order_id, fake = setup_payment()
    fake.completed_overrides = {"amount": "19.99"}
    run_return(fake)

    with database.SessionLocal() as db:
        details = [e.detail or "" for e in db.query(PaymentEvent).filter(PaymentEvent.plan_id == plan_id)]

    assert all(len(d) < 60 and " " not in d for d in details)


# --------------------------------------------------------------------------
# Consistent reads (regression: torn plan/ledger read -> false status_mismatch)
# --------------------------------------------------------------------------


def _count_selects(action):
    from sqlalchemy import event

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", record)
    try:
        action()
    finally:
        event.remove(database.engine, "before_cursor_execute", record)
    return len(statements)


def test_snapshot_reads_plan_and_ledger_in_a_single_statement():
    """Two separate SELECTs can straddle another callback's commit."""
    plan_id, order_id, _ = setup_payment()

    assert _count_selects(lambda: payments.load_snapshot(order_id)) == 1


def test_verified_flag_reads_plan_and_ledger_in_a_single_statement():
    plan_id, order_id, _ = setup_payment()

    assert _count_selects(lambda: payments.payment_verified_for_plan(plan_id)) == 1


def test_concurrent_callbacks_never_report_a_false_plan_ledger_disagreement():
    """Stress version of the same bug: repeat the race many times."""
    outcomes = []

    for _ in range(25):
        plan_id, order_id, fake = setup_payment()
        fake.lookup_delay = 0.03
        barrier = threading.Barrier(6)
        results = []

        def worker():
            barrier.wait(timeout=10)
            results.append(run_return(fake))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert fake.capture_calls == 1
        assert read_state(plan_id)[:2] == ("completed", "completed")
        outcomes.extend((r.result, r.reason) for r in results)

    assert ("review_required", "status_mismatch") not in outcomes
    assert all(result in ("success", "processing") for result, _ in outcomes)
