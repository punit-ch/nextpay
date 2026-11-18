"""Cancellation / capture race regression tests.

Scenario numbers match the bug-investigation brief (1-14). Scenarios 15-18
are frontend behaviour and live in frontend/src/paymentState.test.js.

Everything is deterministic: PayPal is a fake and the racy instants are
reproduced with hooks, not sleeps and luck.
"""

import asyncio

import pytest

from app import payments
from tests.test_payment_service import (
    FakePayPal,
    assert_consistent,
    cancel,
    event_types,
    make_stale,
    read_state,
    run_return,
    setup_payment,
)

AWAITING = ("awaiting_paypal_approval", "awaiting_paypal_approval")
CAPTURING = ("capturing", "capturing")
CANCELLED = ("cancelled_by_buyer", "cancelled_by_buyer")
COMPLETED = ("completed", "completed")


class RacingPayPal(FakePayPal):
    """FakePayPal with hooks that run at precise points of a callback."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.after_lookup = None  # coroutine function run after the lookup answer
        self.capture_delay = 0

    async def get_order(self, order_id):
        order = await super().get_order(order_id)
        if self.after_lookup is not None:
            await self.after_lookup()
        return order

    async def capture_order(self, order_id):
        if self.capture_delay:
            await asyncio.sleep(self.capture_delay)
        return await super().capture_order(order_id)


def racing_payment(**kwargs):
    plan_id, order_id = payments_setup(**kwargs)
    return plan_id, order_id, RacingPayPal(plan_id, order_id)


def payments_setup(**kwargs):
    from tests.helpers import make_plan_and_payment

    return make_plan_and_payment(**kwargs)


@pytest.fixture
def waiting(monkeypatch):
    """Let callbacks wait for an in-flight capture (as in production)."""
    monkeypatch.setenv("CALLBACK_WAIT_SECONDS", "5")
    monkeypatch.setattr(payments, "_WAIT_POLL_INTERVAL", 0.02)


# --- 1 ---------------------------------------------------------------------


def test_scenario_01_buyer_cancels_while_awaiting_approval():
    plan_id, order_id, fake = racing_payment()

    outcome = cancel(plan_id, order_id)

    assert outcome.result == "cancelled"
    assert read_state(plan_id) == CANCELLED + (None, None)
    assert (fake.lookup_calls, fake.capture_calls) == (0, 0)
    assert event_types(plan_id) == ["buyer_cancelled"]


# --- 2 ---------------------------------------------------------------------


def test_scenario_02a_cancel_lands_between_lookup_and_claim_so_nothing_is_captured():
    """The order IS approved at PayPal, but the cancel wins the state change
    first. The return callback must then not capture."""
    plan_id, order_id, fake = racing_payment()

    async def cancel_now():
        fake.after_lookup = None
        await payments.cancel_checkout(plan_id, order_id)

    fake.after_lookup = cancel_now

    outcome = run_return(fake)

    assert outcome.result == "cancelled"
    assert fake.capture_calls == 0
    assert read_state(plan_id)[:2] == CANCELLED
    assert_consistent(plan_id)


def test_scenario_02b_cancel_during_return_check_of_an_unapproved_order():
    """The original bug: the return callback used to hold a `capturing` claim
    while merely checking an order the buyer had not approved, and the cancel
    callback was told 'processing' and then lost."""
    plan_id, order_id, fake = racing_payment()
    fake.order_override = {"id": order_id, "status": "CREATED"}
    fake.lookup_delay = 0.2

    async def scenario():
        returning = asyncio.create_task(
            payments.process_paypal_return(
                order_id, get_order=fake.get_order, capture_order=fake.capture_order
            )
        )
        await asyncio.sleep(0.05)  # return callback is mid-lookup
        cancelled = await payments.cancel_checkout(plan_id, order_id)
        return cancelled, await returning

    cancelled, returned = asyncio.run(scenario())

    assert cancelled.result == "cancelled"
    assert returned.result == "not_approved"
    assert fake.capture_calls == 0
    assert read_state(plan_id)[:2] == CANCELLED
    assert "capture_claimed" not in event_types(plan_id)


# --- 3 ---------------------------------------------------------------------


def test_scenario_03_cancel_during_capture_never_cancels_and_reports_the_truth(waiting):
    plan_id, order_id, fake = racing_payment()
    fake.capture_delay = 0.3

    async def scenario():
        returning = asyncio.create_task(
            payments.process_paypal_return(
                order_id, get_order=fake.get_order, capture_order=fake.capture_order
            )
        )
        await asyncio.sleep(0.1)  # capture is in flight at PayPal
        assert read_state(plan_id)[:2] == CAPTURING
        cancelled = await payments.cancel_checkout(plan_id, order_id)
        return cancelled, await returning

    cancelled, returned = asyncio.run(scenario())

    assert returned.result == "success"
    assert cancelled.result == "success"  # the buyer sees what really happened
    assert fake.capture_calls == 1
    assert read_state(plan_id) == COMPLETED + (fake.capture_id, fake.capture_id)
    assert "buyer_cancelled" not in event_types(plan_id)


def test_scenario_03b_cancel_during_capture_without_waiting_changes_nothing():
    plan_id, order_id, fake = racing_payment()
    payments.apply_transition(
        plan_id, order_id, payments.AWAITING, payments.CAPTURING,
        event_type="capture_claimed",
    )

    outcome = cancel(plan_id, order_id, fake)

    assert outcome.result == "processing"
    assert outcome.reason == "capture_in_progress"
    assert read_state(plan_id)[:2] == CAPTURING  # never forced to cancelled
    assert fake.lookup_calls == 0  # a live claim is not second-guessed


# --- 4 ---------------------------------------------------------------------


def test_scenario_04_capture_completes_just_before_the_cancel_callback():
    plan_id, order_id, fake = racing_payment()
    run_return(fake)

    outcome = cancel(plan_id, order_id, fake)

    assert outcome.result == "success"
    assert read_state(plan_id) == COMPLETED + (fake.capture_id, fake.capture_id)
    assert "buyer_cancelled" not in event_types(plan_id)


# --- 5 and 6 ---------------------------------------------------------------


def test_scenario_05_cancel_arrives_before_the_return_callback():
    plan_id, order_id, fake = racing_payment()

    assert cancel(plan_id, order_id).result == "cancelled"

    # PayPal now (wrongly or late) reports the order approved. Still no capture.
    outcome = run_return(fake)

    assert outcome.result == "cancelled"
    assert fake.capture_calls == 0
    assert read_state(plan_id)[:2] == CANCELLED


def test_scenario_06_return_callback_after_cancellation_never_touches_paypal():
    plan_id, order_id, fake = racing_payment()
    cancel(plan_id, order_id)

    for _ in range(3):
        assert run_return(fake).result == "cancelled"

    assert (fake.lookup_calls, fake.capture_calls) == (0, 0)


# --- 7 and 8 ---------------------------------------------------------------


def test_scenario_07_duplicate_cancellation_callback_is_idempotent():
    plan_id, order_id, fake = racing_payment()

    results = [cancel(plan_id, order_id) for _ in range(3)]

    assert [r.result for r in results] == ["cancelled"] * 3
    assert read_state(plan_id)[:2] == CANCELLED
    assert event_types(plan_id) == ["buyer_cancelled"]  # recorded once


def test_scenario_08_duplicate_return_callback_does_not_capture_twice():
    plan_id, order_id, fake = racing_payment()

    results = [run_return(fake) for _ in range(3)]

    assert [r.result for r in results] == ["success"] * 3
    assert fake.capture_calls == 1
    assert read_state(plan_id) == COMPLETED + (fake.capture_id, fake.capture_id)


def test_scenario_08b_second_return_callback_during_capture_waits_for_the_result(waiting):
    plan_id, order_id, fake = racing_payment()
    fake.capture_delay = 0.3

    async def scenario():
        first = asyncio.create_task(
            payments.process_paypal_return(
                order_id, get_order=fake.get_order, capture_order=fake.capture_order
            )
        )
        await asyncio.sleep(0.1)
        second = await payments.process_paypal_return(
            order_id, get_order=fake.get_order, capture_order=fake.capture_order
        )
        return await first, second

    first, second = asyncio.run(scenario())

    # The duplicate lands on the FINAL result, not a transient "pending" page.
    assert (first.result, second.result) == ("success", "success")
    assert fake.capture_calls == 1


# --- 9 ---------------------------------------------------------------------


@pytest.mark.parametrize("token", ["SOMEONE-ELSES-ORDER", "x", "../../etc"])
def test_scenario_09_invalid_cancel_token_changes_nothing(token):
    plan_id, order_id, fake = racing_payment()

    outcome = cancel(plan_id, token, fake)

    assert outcome.result == "invalid_state"
    assert outcome.reason == "order_mismatch"
    assert read_state(plan_id)[:2] == AWAITING
    assert (fake.lookup_calls, fake.capture_calls) == (0, 0)
    assert event_types(plan_id) == []


def test_scenario_09b_invalid_return_token_changes_nothing():
    plan_id, order_id, fake = racing_payment()

    assert run_return(fake, order_id="NOT-THIS-ORDER").result == "not_found"
    assert read_state(plan_id)[:2] == AWAITING


# --- 10 --------------------------------------------------------------------


def test_scenario_10a_expired_lease_is_reconciled_with_paypal():
    plan_id, order_id, fake = racing_payment(payment_status="capturing")
    make_stale(plan_id)

    outcome = run_return(fake)

    assert outcome.result == "success"
    assert fake.capture_calls == 1
    assert read_state(plan_id) == COMPLETED + (fake.capture_id, fake.capture_id)
    assert "capture_reclaimed" in event_types(plan_id)


def test_scenario_10b_stale_claim_for_an_unapproved_order_can_be_cancelled():
    plan_id, order_id, fake = racing_payment(payment_status="capturing")
    fake.order_override = {"id": order_id, "status": "CREATED"}
    make_stale(plan_id)

    outcome = cancel(plan_id, order_id, fake)

    assert outcome.result == "cancelled"
    assert fake.capture_calls == 0
    assert read_state(plan_id)[:2] == CANCELLED
    assert event_types(plan_id) == ["claim_released", "buyer_cancelled"]


@pytest.mark.parametrize("paypal_status", ["APPROVED", "COMPLETED", "VOIDED", "WEIRD"])
def test_scenario_10c_stale_claim_cancel_is_refused_when_paypal_says_approved(paypal_status):
    """PayPal may already have captured: cancel must NOT override it."""
    plan_id, order_id, fake = racing_payment(payment_status="capturing")
    fake.order_override = {"id": order_id, "status": paypal_status}
    make_stale(plan_id)

    outcome = cancel(plan_id, order_id, fake)

    assert outcome.result == "pending"
    assert outcome.reason == "cancel_conflict"
    assert read_state(plan_id)[:2] == CAPTURING
    assert fake.capture_calls == 0  # a cancel never triggers a capture


def test_scenario_10d_stale_claim_cancel_with_paypal_down_is_not_invented():
    from app.paypal_client import PayPalUnavailableError

    plan_id, order_id, fake = racing_payment(payment_status="capturing")
    fake.lookup_error = PayPalUnavailableError("down")
    make_stale(plan_id)

    outcome = cancel(plan_id, order_id, fake)

    assert outcome.result == "pending"
    assert read_state(plan_id)[:2] == CAPTURING


def test_scenario_10e_stale_claim_refreshed_by_someone_else_is_not_cancelled():
    """Compare-and-swap: if another callback refreshed the claim after we read
    it, our cancel must back off."""
    plan_id, order_id, fake = racing_payment(payment_status="capturing")
    fake.order_override = {"id": order_id, "status": "CREATED"}
    make_stale(plan_id)

    async def refresh_claim():
        # A concurrent reclaimer takes the stale claim while we look at PayPal.
        snapshot = payments.load_snapshot(order_id)
        assert payments._reclaim_stale_capture(snapshot)

    fake.after_lookup = refresh_claim

    outcome = cancel(plan_id, order_id, fake)

    assert outcome.result != "cancelled"
    assert read_state(plan_id)[:2] == CAPTURING


# --- 11, 12, 13 ------------------------------------------------------------


def test_scenario_11_pending_capture_is_neither_success_nor_failure():
    plan_id, order_id, fake = racing_payment()
    fake.completed_overrides = {"capture_status": "PENDING"}

    outcome = run_return(fake)

    assert outcome.result == "pending"
    assert read_state(plan_id) == CAPTURING + (None, None)


def test_scenario_12_completed_capture_records_the_verified_capture_id():
    plan_id, order_id, fake = racing_payment()

    outcome = run_return(fake)

    assert outcome.result == "success"
    assert read_state(plan_id) == COMPLETED + (fake.capture_id, fake.capture_id)
    assert payments.payment_verified_for_plan(plan_id) is True


def test_scenario_13_capture_amount_mismatch_requires_review_and_is_not_verified():
    plan_id, order_id, fake = racing_payment()
    fake.completed_overrides = {"amount": "19.99"}

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert read_state(plan_id) == ("review_required", "review_required", None, None)
    assert payments.payment_verified_for_plan(plan_id) is False


# --- 14 --------------------------------------------------------------------


def test_scenario_14a_plan_and_ledger_disagreeing_is_flagged_on_return():
    from sqlalchemy import update

    from app import database
    from app.models import Payment

    plan_id, order_id, fake = racing_payment()
    with database.SessionLocal() as db:
        db.execute(update(Payment).where(Payment.plan_id == plan_id).values(status="capturing"))
        db.commit()

    outcome = run_return(fake)

    assert outcome.result == "review_required"
    assert outcome.reason == "status_mismatch"
    assert (fake.lookup_calls, fake.capture_calls) == (0, 0)
    assert payments.payment_verified_for_plan(plan_id) is False


def test_scenario_14b_cancel_does_not_paper_over_a_disagreement():
    from sqlalchemy import update

    from app import database
    from app.models import Payment

    plan_id, order_id, fake = racing_payment()
    with database.SessionLocal() as db:
        db.execute(update(Payment).where(Payment.plan_id == plan_id).values(status="completed"))
        db.commit()

    outcome = cancel(plan_id, order_id, fake)

    assert outcome.result == "review_required"
    # Neither table was changed by the cancel attempt.
    assert read_state(plan_id)[:2] == ("awaiting_paypal_approval", "completed")


@pytest.mark.parametrize(
    "mutate",
    [
        {"plan_capture": None},                      # completed, no capture id
        {"ledger_capture": "A-DIFFERENT-CAPTURE"},   # ids disagree
        {"ledger_status": "capturing"},              # ledger lags the plan
    ],
)
def test_completed_without_consistent_records_is_never_verified(mutate):
    from sqlalchemy import update

    from app import database
    from app.models import Payment, PurchasePlan

    plan_id, order_id, fake = racing_payment()
    run_return(fake)
    assert payments.payment_verified_for_plan(plan_id) is True

    with database.SessionLocal() as db:
        if "plan_capture" in mutate:
            db.execute(update(PurchasePlan).where(PurchasePlan.plan_id == plan_id).values(paypal_capture_id=mutate["plan_capture"]))
        if "ledger_capture" in mutate:
            db.execute(update(Payment).where(Payment.plan_id == plan_id).values(paypal_capture_id=mutate["ledger_capture"]))
        if "ledger_status" in mutate:
            db.execute(update(Payment).where(Payment.plan_id == plan_id).values(status=mutate["ledger_status"]))
        db.commit()

    assert payments.payment_verified_for_plan(plan_id) is False


@pytest.mark.parametrize(
    "status",
    ["awaiting_paypal_approval", "capturing", "cancelled_by_buyer", "failed", "review_required"],
)
def test_only_a_completed_payment_is_ever_verified(status):
    plan_id, order_id, fake = racing_payment(payment_status=status)

    assert payments.payment_verified_for_plan(plan_id) is False
