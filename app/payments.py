"""Payment lifecycle service.

This module owns every change to ``PurchasePlan.payment_status`` and
``Payment.status``. It has no FastAPI imports, so it can be tested directly.

State machine (identical vocabulary on the plan and on the payment ledger)::

    not_started -> awaiting_paypal_approval
    awaiting_paypal_approval -> capturing | cancelled_by_buyer
    capturing -> completed | review_required | failed
                 | awaiting_paypal_approval   (buyer had not really approved)
    completed, cancelled_by_buyer, review_required, failed -> (terminal)

``review_required`` is resolved by a human, outside the application.

Rules that keep the system safe:

* Every transition is one conditional UPDATE per table ("... WHERE status =
  <expected>") inside ONE transaction, so two callbacks cannot both claim the
  same payment and the plan and ledger can never disagree.
* No database session is open while we talk to PayPal.
* PayPal's own order state is authoritative. A timeout or error never marks a
  payment failed; the row stays ``capturing`` and the next callback
  reconciles against PayPal (the capture request ID is deterministic, so a
  repeated capture can never charge twice).
* Only an explicit PayPal answer moves a payment to ``failed``; only a
  confirmed mismatch moves it to ``review_required``.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app import config, database
from app.models import Payment, PaymentEvent, PurchasePlan
from app.payment_verification import (
    CURRENCY,
    ExpectedPayment,
    PaymentMismatch,
    PaymentPending,
    verify_completed_order,
    verify_order_before_capture,
)
from app.paypal_client import (
    PayPalAuthError,
    PayPalConfigError,
    PayPalError,
    PayPalMalformedResponseError,
    PayPalRejectedError,
    PayPalUnavailableError,
    is_valid_order_id,
)
from app.policy import validate_purchase

logger = logging.getLogger("nextpay.payments")

# --- Payment statuses -------------------------------------------------------

NOT_STARTED = "not_started"
AWAITING = "awaiting_paypal_approval"
CAPTURING = "capturing"
COMPLETED = "completed"
CANCELLED = "cancelled_by_buyer"
REVIEW = "review_required"
FAILED = "failed"

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    NOT_STARTED: frozenset({AWAITING}),
    AWAITING: frozenset({CAPTURING, CANCELLED}),
    CAPTURING: frozenset({COMPLETED, REVIEW, FAILED, AWAITING}),
    COMPLETED: frozenset(),
    CANCELLED: frozenset(),
    REVIEW: frozenset(),
    FAILED: frozenset(),
}

TERMINAL_STATUSES = frozenset(
    status for status, targets in ALLOWED_TRANSITIONS.items() if not targets
)


class InvalidTransition(Exception):
    pass


class PaymentServiceError(Exception):
    status_code = 400


class PlanNotFound(PaymentServiceError):
    status_code = 404


class ApprovalRequired(PaymentServiceError):
    status_code = 403


class PaymentAlreadyStarted(PaymentServiceError):
    status_code = 409


class LedgerLookupError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ReturnOutcome:
    """What the API layer should tell the buyer.

    result: success | processing | pending | review_required | failed |
            cancelled | not_approved | not_found | invalid_state
    """

    result: str
    plan_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """Immutable copy of the rows we need, so no session stays open."""

    plan_id: str
    payment_id: str
    order_id: str
    plan_status: str
    payment_status: str
    plan_order_id: str | None
    payment_order_id: str
    payment_plan_id: str
    plan_total: str
    unit_price: str
    quantity: int
    budget_limit: str
    payment_amount: str
    payment_currency: str
    payment_updated_at: datetime | None


# --- Small helpers ----------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _add_event(
    db,
    plan_id: str,
    order_id: str | None,
    event_type: str,
    from_status: str | None = None,
    to_status: str | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        PaymentEvent(
            plan_id=plan_id,
            paypal_order_id=order_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            detail=detail[:200] if detail else None,
        )
    )


def record_event(
    plan_id: str,
    order_id: str | None,
    event_type: str,
    detail: str | None = None,
) -> None:
    """Best-effort audit entry that does not change state."""
    try:
        with database.SessionLocal() as db:
            _add_event(db, plan_id, order_id, event_type, detail=detail)
            db.commit()
    except Exception:  # auditing must never break payment handling
        logger.exception("Could not record payment event %s", event_type)


# --- Atomic state transitions ----------------------------------------------


def transition_payment(
    db,
    *,
    plan_id: str,
    order_id: str,
    from_status: str,
    to_status: str,
    capture_id: str | None = None,
    event_type: str,
    detail: str | None = None,
) -> bool:
    """Move plan and ledger together. Caller commits. False = lost the race."""
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, frozenset()):
        raise InvalidTransition(f"{from_status} -> {to_status} is not allowed.")

    now = _utcnow()
    plan_values = {"payment_status": to_status, "updated_at": now}
    payment_values = {"status": to_status, "updated_at": now}

    if capture_id is not None:
        plan_values["paypal_capture_id"] = capture_id
        payment_values["paypal_capture_id"] = capture_id

    plan_result = db.execute(
        update(PurchasePlan)
        .where(
            PurchasePlan.plan_id == plan_id,
            PurchasePlan.paypal_order_id == order_id,
            PurchasePlan.payment_status == from_status,
        )
        .values(**plan_values)
    )
    payment_result = db.execute(
        update(Payment)
        .where(
            Payment.plan_id == plan_id,
            Payment.paypal_order_id == order_id,
            Payment.status == from_status,
        )
        .values(**payment_values)
    )

    if plan_result.rowcount != 1 or payment_result.rowcount != 1:
        db.rollback()
        return False

    _add_event(db, plan_id, order_id, event_type, from_status, to_status, detail)
    return True


def apply_transition(
    plan_id: str,
    order_id: str,
    from_status: str,
    to_status: str,
    *,
    capture_id: str | None = None,
    event_type: str,
    detail: str | None = None,
) -> bool:
    """Run one transition in its own short transaction."""
    with database.SessionLocal() as db:
        moved = transition_payment(
            db,
            plan_id=plan_id,
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            capture_id=capture_id,
            event_type=event_type,
            detail=detail,
        )
        if moved:
            db.commit()
        return moved


# --- Reading the ledger -----------------------------------------------------


def _snapshot_from_rows(plan: PurchasePlan, payment: Payment) -> Snapshot:
    return Snapshot(
        plan_id=plan.plan_id,
        payment_id=payment.payment_id,
        order_id=payment.paypal_order_id,
        plan_status=plan.payment_status,
        payment_status=payment.status,
        plan_order_id=plan.paypal_order_id,
        payment_order_id=payment.paypal_order_id,
        payment_plan_id=payment.plan_id,
        plan_total=plan.total,
        unit_price=plan.unit_price,
        quantity=plan.quantity,
        budget_limit=plan.budget_limit,
        payment_amount=payment.amount,
        payment_currency=payment.currency,
        payment_updated_at=payment.updated_at,
    )


def load_snapshot(order_id: str) -> Snapshot:
    with database.SessionLocal() as db:
        payment = (
            db.query(Payment).filter(Payment.paypal_order_id == order_id).first()
        )

        if payment is None:
            plan_exists = (
                db.query(PurchasePlan.plan_id)
                .filter(PurchasePlan.paypal_order_id == order_id)
                .first()
            )
            raise LedgerLookupError(
                "payment_record_not_found" if plan_exists else "plan_not_found"
            )

        plan = db.get(PurchasePlan, payment.plan_id)
        if plan is None:
            raise LedgerLookupError("plan_not_found")

        return _snapshot_from_rows(plan, payment)


_STATUS_TO_RESULT = {
    COMPLETED: "success",
    CAPTURING: "processing",
    AWAITING: "processing",
    CANCELLED: "cancelled",
    REVIEW: "review_required",
    FAILED: "failed",
}


def _outcome_for_status(plan_id: str, status: str) -> ReturnOutcome:
    return ReturnOutcome(
        _STATUS_TO_RESULT.get(status, "invalid_state"),
        plan_id,
        status,
    )


def _settled_outcome(order_id: str, plan_id: str) -> ReturnOutcome:
    """We lost a race: report whatever state the winner left behind."""
    try:
        snapshot = load_snapshot(order_id)
    except LedgerLookupError as exc:
        return ReturnOutcome("not_found", plan_id, exc.reason)

    if snapshot.plan_status != snapshot.payment_status:
        return ReturnOutcome("review_required", plan_id, "status_mismatch")

    return _outcome_for_status(plan_id, snapshot.plan_status)


def get_payment_ledger(plan_id: str) -> dict | None:
    """Ledger rows and audit trail for one plan (for the API and the demo)."""
    with database.SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)
        if plan is None:
            return None

        payments = (
            db.query(Payment)
            .filter(Payment.plan_id == plan_id)
            .order_by(Payment.created_at)
            .all()
        )
        events = (
            db.query(PaymentEvent)
            .filter(PaymentEvent.plan_id == plan_id)
            .order_by(PaymentEvent.event_id)
            .all()
        )

        return {
            "plan_id": plan_id,
            "payment_status": plan.payment_status,
            "payments": [
                {
                    "paypal_order_id": item.paypal_order_id,
                    "paypal_capture_id": item.paypal_capture_id,
                    "amount": item.amount,
                    "currency": item.currency,
                    "status": item.status,
                }
                for item in payments
            ],
            "events": [
                {
                    "event_type": item.event_type,
                    "from_status": item.from_status,
                    "to_status": item.to_status,
                    "detail": item.detail,
                    "created_at": (
                        _as_utc(item.created_at).isoformat()
                        if item.created_at
                        else None
                    ),
                }
                for item in events
            ],
        }


# --- Creating the PayPal order ---------------------------------------------


def _plan_order_response(plan_id, order_id, status, approval_url) -> dict:
    return {
        "success": True,
        "plan_id": plan_id,
        "paypal_order_id": order_id,
        "paypal_status": status,
        "payment_status": AWAITING,
        "approval_url": approval_url,
        "message": "Open the PayPal URL to review and approve your purchase.",
    }


async def create_order_for_plan(plan_id: str, *, create_order) -> dict:
    """Create the PayPal order for an approved plan, exactly once."""
    # 1) Short read-only session: check the plan and re-run the policy.
    with database.SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)

        if plan is None:
            raise PlanNotFound("Plan not found.")

        if plan.status != "approved_by_user":
            raise ApprovalRequired(
                "User approval is required before creating a PayPal order."
            )

        if plan.payment_status != NOT_STARTED:
            raise PaymentAlreadyStarted(
                "Payment has already been started or completed."
            )

        product_id = plan.product_id
        quantity = plan.quantity
        budget_limit = Decimal(plan.budget_limit)

    validated = validate_purchase(  # raises PolicyViolation
        product_id=product_id,
        quantity=quantity,
        budget_limit=budget_limit,
    )
    amount = str(validated["total"])

    # 2) PayPal call with NO database session open. The request ID is
    #    deterministic per plan, so a repeat returns the same order.
    result = await create_order(
        amount=amount,
        description=validated["product_name"],
        plan_id=plan_id,
    )
    order_id = result["paypal_order_id"]

    # 3) Short write session: claim not_started -> awaiting atomically.
    try:
        with database.SessionLocal() as db:
            claimed = db.execute(
                update(PurchasePlan)
                .where(
                    PurchasePlan.plan_id == plan_id,
                    PurchasePlan.status == "approved_by_user",
                    PurchasePlan.payment_status == NOT_STARTED,
                )
                .values(
                    payment_status=AWAITING,
                    paypal_order_id=order_id,
                    updated_at=_utcnow(),
                )
            )

            if claimed.rowcount == 1:
                db.add(
                    Payment(
                        payment_id=str(uuid4()),
                        plan_id=plan_id,
                        paypal_order_id=order_id,
                        amount=amount,
                        currency=CURRENCY,
                        status=AWAITING,
                    )
                )
                _add_event(
                    db,
                    plan_id,
                    order_id,
                    "order_created",
                    NOT_STARTED,
                    AWAITING,
                )
                db.commit()
                return _plan_order_response(
                    plan_id, order_id, result.get("status"), result["approval_url"]
                )

            db.rollback()
    except IntegrityError:
        logger.error("Order ID collision while recording plan=%s", plan_id)
        raise PaymentAlreadyStarted(
            "Payment has already been started or completed."
        ) from None

    # Lost the race. If the winner recorded the same PayPal order (a double
    # click returns the same order), answer idempotently.
    with database.SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)
        if plan is not None and plan.paypal_order_id == order_id:
            return _plan_order_response(
                plan_id, order_id, result.get("status"), result["approval_url"]
            )

    raise PaymentAlreadyStarted("Payment has already been started or completed.")


# --- Buyer cancellation -----------------------------------------------------


def cancel_checkout(plan_id: str, order_token: str | None = None) -> ReturnOutcome:
    """Handle PayPal's cancel redirect. Never overrides a later state."""
    with database.SessionLocal() as db:
        payment = (
            db.query(Payment)
            .filter(Payment.plan_id == plan_id)
            .order_by(Payment.created_at.desc())
            .first()
        )
        plan = db.get(PurchasePlan, plan_id)

        if plan is None or payment is None:
            # Nothing to cancel; do not reveal whether the plan exists.
            return ReturnOutcome("cancelled", plan_id, "no_payment")

        order_id = payment.paypal_order_id
        plan_order_id = plan.paypal_order_id

    # PayPal appends ?token=<order id>. When present it must match.
    if order_token is not None and order_token != plan_order_id:
        logger.warning("Cancel callback with mismatching order plan=%s", plan_id)
        return ReturnOutcome("invalid_state", plan_id, "order_mismatch")

    if apply_transition(
        plan_id,
        order_id,
        AWAITING,
        CANCELLED,
        event_type="buyer_cancelled",
    ):
        return ReturnOutcome("cancelled", plan_id, CANCELLED)

    return _settled_outcome(order_id, plan_id)


# --- Return callback and reconciliation ------------------------------------


def _lease_active(snapshot: Snapshot) -> bool:
    updated = _as_utc(snapshot.payment_updated_at)
    if updated is None:
        return False
    lease = timedelta(seconds=config.capture_lease_seconds())
    return _utcnow() - updated < lease


def _reclaim_stale_capture(snapshot: Snapshot) -> bool:
    """Take over a `capturing` claim whose owner seems to have died.

    Compare-and-swap on updated_at: only one caller can win.
    """
    now = _utcnow()

    with database.SessionLocal() as db:
        payment_result = db.execute(
            update(Payment)
            .where(
                Payment.payment_id == snapshot.payment_id,
                Payment.status == CAPTURING,
                Payment.updated_at == snapshot.payment_updated_at,
            )
            .values(updated_at=now)
        )

        if payment_result.rowcount != 1:
            db.rollback()
            return False

        plan_result = db.execute(
            update(PurchasePlan)
            .where(
                PurchasePlan.plan_id == snapshot.plan_id,
                PurchasePlan.paypal_order_id == snapshot.order_id,
                PurchasePlan.payment_status == CAPTURING,
            )
            .values(updated_at=now)
        )

        if plan_result.rowcount != 1:
            db.rollback()
            return False

        _add_event(
            db,
            snapshot.plan_id,
            snapshot.order_id,
            "capture_reclaimed",
            CAPTURING,
            CAPTURING,
            "stale_claim",
        )
        db.commit()
        return True


def _expected_from_ledger(snapshot: Snapshot) -> ExpectedPayment:
    """Check our own records are consistent BEFORE touching PayPal."""
    if (
        snapshot.plan_order_id != snapshot.order_id
        or snapshot.payment_order_id != snapshot.order_id
        or snapshot.payment_plan_id != snapshot.plan_id
    ):
        raise PaymentMismatch("ownership_mismatch")

    if snapshot.payment_currency != CURRENCY:
        raise PaymentMismatch("ledger_currency_mismatch")

    try:
        plan_total = Decimal(snapshot.plan_total)
        unit_price = Decimal(snapshot.unit_price)
        ledger_amount = Decimal(snapshot.payment_amount)
        budget = Decimal(snapshot.budget_limit)
    except InvalidOperation:
        raise PaymentMismatch("ledger_amount_invalid") from None

    if ledger_amount <= 0 or ledger_amount != plan_total:
        raise PaymentMismatch("ledger_amount_mismatch")

    if unit_price * snapshot.quantity != plan_total:
        raise PaymentMismatch("plan_total_mismatch")

    if plan_total > budget:
        raise PaymentMismatch("budget_exceeded")

    return ExpectedPayment(
        order_id=snapshot.order_id,
        plan_id=snapshot.plan_id,
        amount=ledger_amount,
    )


def _error_code(exc: PayPalError) -> str:
    if isinstance(exc, PayPalUnavailableError):
        return "paypal_unavailable"
    if isinstance(exc, (PayPalAuthError, PayPalConfigError)):
        return "paypal_configuration"
    if isinstance(exc, PayPalMalformedResponseError):
        return "malformed_response"
    if isinstance(exc, PayPalRejectedError):
        return "paypal_rejected"
    return "paypal_error"


def _pending(snapshot: Snapshot, code: str) -> ReturnOutcome:
    """Outcome unknown: leave the state alone so it can be reconciled."""
    logger.warning(
        "Payment status unknown, will reconcile later plan=%s order=%s code=%s",
        snapshot.plan_id,
        snapshot.order_id,
        code,
    )
    record_event(snapshot.plan_id, snapshot.order_id, "reconcile_pending", code)
    return ReturnOutcome("pending", snapshot.plan_id, code)


def _finish(
    snapshot: Snapshot,
    to_status: str,
    result: str,
    code: str,
    event_type: str,
    capture_id: str | None = None,
) -> ReturnOutcome:
    try:
        moved = apply_transition(
            snapshot.plan_id,
            snapshot.order_id,
            CAPTURING,
            to_status,
            capture_id=capture_id,
            event_type=event_type,
            detail=code,
        )
    except IntegrityError:
        # The capture ID is already attached to a different payment.
        logger.error(
            "Capture ID conflict plan=%s order=%s", snapshot.plan_id, snapshot.order_id
        )
        return _finish(
            snapshot,
            REVIEW,
            "review_required",
            "capture_id_conflict",
            "review_required",
        )

    if not moved:
        return _settled_outcome(snapshot.order_id, snapshot.plan_id)

    if to_status == REVIEW:
        logger.error(
            "Payment needs manual review plan=%s order=%s code=%s",
            snapshot.plan_id,
            snapshot.order_id,
            code,
        )
    else:
        logger.info(
            "Payment %s plan=%s order=%s code=%s",
            to_status,
            snapshot.plan_id,
            snapshot.order_id,
            code,
        )

    return ReturnOutcome(result, snapshot.plan_id, code)


async def _reconcile(snapshot: Snapshot, get_order, capture_order) -> ReturnOutcome:
    """Decide the payment's fate from PayPal's authoritative state."""
    order_id = snapshot.order_id

    try:
        expected = _expected_from_ledger(snapshot)
    except PaymentMismatch as exc:
        # Our own records are inconsistent: do not capture anything.
        return _finish(snapshot, REVIEW, "review_required", exc.code, "review_required")

    try:
        order = await get_order(order_id)
        verify_order_before_capture(order, expected)

        status = order.get("status")

        if status == "COMPLETED":
            result = order

        elif status == "APPROVED":
            try:
                result = await capture_order(order_id)
            except PayPalRejectedError as exc:
                if exc.issue == "INSTRUMENT_DECLINED":
                    return _finish(
                        snapshot, FAILED, "failed", "declined", "payment_failed"
                    )
                if exc.issue == "ORDER_ALREADY_CAPTURED":
                    result = await get_order(order_id)
                else:
                    return _pending(snapshot, _error_code(exc))

        elif status == "VOIDED":
            return _finish(
                snapshot, FAILED, "failed", "order_voided", "payment_failed"
            )

        elif status in ("CREATED", "SAVED", "PAYER_ACTION_REQUIRED"):
            # The buyer has not finished approving: go back to waiting.
            if apply_transition(
                snapshot.plan_id,
                order_id,
                CAPTURING,
                AWAITING,
                event_type="claim_released",
                detail="order_not_approved",
            ):
                return ReturnOutcome("not_approved", snapshot.plan_id, "order_not_approved")
            return _settled_outcome(order_id, snapshot.plan_id)

        else:
            return _pending(snapshot, "unexpected_order_status")

        capture = verify_completed_order(result, expected)

    except PaymentMismatch as exc:
        return _finish(snapshot, REVIEW, "review_required", exc.code, "review_required")
    except PaymentPending as exc:
        return _pending(snapshot, exc.code)
    except PayPalError as exc:
        return _pending(snapshot, _error_code(exc))
    except Exception:
        # A bug or database error. Never report failure: the capture may have
        # succeeded. Log it loudly and keep the payment recoverable.
        logger.exception(
            "Unexpected error while reconciling plan=%s order=%s",
            snapshot.plan_id,
            order_id,
        )
        return _pending(snapshot, "internal_error")

    try:
        return _finish(
            snapshot,
            COMPLETED,
            "success",
            "captured",
            "capture_completed",
            capture_id=capture.capture_id,
        )
    except Exception:
        # PayPal says paid but our write failed. The next callback will find
        # the order COMPLETED at PayPal and finish without capturing again.
        logger.exception(
            "Could not record completed payment plan=%s order=%s",
            snapshot.plan_id,
            order_id,
        )
        return ReturnOutcome("pending", snapshot.plan_id, "database_error")


async def process_paypal_return(
    order_id: str,
    *,
    get_order,
    capture_order,
) -> ReturnOutcome:
    """Handle PayPal's return redirect, a refresh, or a manual re-check."""
    if not is_valid_order_id(order_id):
        return ReturnOutcome("not_found", None, "invalid_token")

    try:
        snapshot = load_snapshot(order_id)
    except LedgerLookupError as exc:
        return ReturnOutcome("not_found", None, exc.reason)

    plan_id = snapshot.plan_id

    if snapshot.plan_status != snapshot.payment_status:
        logger.error(
            "Plan and ledger disagree plan=%s plan_status=%s payment_status=%s",
            plan_id,
            snapshot.plan_status,
            snapshot.payment_status,
        )
        if snapshot.plan_status == CAPTURING and snapshot.payment_status == AWAITING:
            if _lease_active(snapshot):
                return ReturnOutcome("processing", plan_id, "capture_in_progress")
            if _reclaim_stale_capture(snapshot):
                return ReturnOutcome("processing", plan_id, "capture_in_progress")
            return ReturnOutcome("processing", plan_id, "capture_in_progress")
        return ReturnOutcome("review_required", plan_id, "status_mismatch")

    if snapshot.plan_order_id != order_id or snapshot.payment_plan_id != plan_id:
        logger.error("Order ownership mismatch plan=%s order=%s", plan_id, order_id)
        return ReturnOutcome("review_required", plan_id, "ownership_mismatch")

    status = snapshot.plan_status

    if status == AWAITING:
        if not apply_transition(
            plan_id,
            order_id,
            AWAITING,
            CAPTURING,
            event_type="capture_claimed",
        ):
            # Another callback claimed it first.
            return _settled_outcome(order_id, plan_id)

    elif status == CAPTURING:
        if _lease_active(snapshot):
            return ReturnOutcome("processing", plan_id, "capture_in_progress")
        if not _reclaim_stale_capture(snapshot):
            return ReturnOutcome("processing", plan_id, "capture_in_progress")

    else:
        # completed, cancelled, failed, review_required or unknown.
        return _outcome_for_status(plan_id, status)

    return await _reconcile(snapshot, get_order, capture_order)


async def reconcile_plan(plan_id: str, *, get_order, capture_order) -> ReturnOutcome:
    """Manual re-check using the plan's own stored PayPal order ID."""
    with database.SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)
        if plan is None:
            raise PlanNotFound("Plan not found.")
        order_id = plan.paypal_order_id

    if not order_id:
        return ReturnOutcome("invalid_state", plan_id, NOT_STARTED)

    return await process_paypal_return(
        order_id,
        get_order=get_order,
        capture_order=capture_order,
    )
