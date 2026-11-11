"""Shared builders for payment tests (no FastAPI required)."""

from uuid import uuid4

from app import database
from app.models import Payment, PurchasePlan


def make_plan_and_payment(
    *,
    payment_status="awaiting_paypal_approval",
    product_id="desk_lamp",
    product_name="Adjustable LED Desk Lamp",
    unit_price="24.99",
    quantity=1,
    total="24.99",
    budget_limit="100.00",
    ledger_amount=None,
    ledger_currency="USD",
    access_token_hash=None,
):
    """Insert a plan + ledger row already linked to a PayPal order."""
    plan_id = str(uuid4())
    order_id = f"TEST-ORDER-{uuid4()}"

    with database.SessionLocal() as db:
        db.add(
            PurchasePlan(
                plan_id=plan_id,
                goal="Test goal",
                product_id=product_id,
                product_name=product_name,
                quantity=quantity,
                unit_price=unit_price,
                total=total,
                budget_limit=budget_limit,
                status="approved_by_user",
                payment_status=payment_status,
                paypal_order_id=order_id,
                access_token_hash=access_token_hash,
            )
        )
        db.flush()  # the plan must exist before the ledger row (foreign key)
        db.add(
            Payment(
                payment_id=str(uuid4()),
                plan_id=plan_id,
                paypal_order_id=order_id,
                amount=ledger_amount or total,
                currency=ledger_currency,
                status=payment_status,
            )
        )
        db.commit()

    return plan_id, order_id


def paypal_order(
    order_id,
    plan_id,
    *,
    status="COMPLETED",
    amount="24.99",
    currency="USD",
    capture_id=None,
    capture_status="COMPLETED",
    reference_id=None,
    custom_id=None,
    captures=None,
):
    """Build a PayPal-shaped order payload."""
    if captures is None:
        capture = {
            "status": capture_status,
            "amount": {"currency_code": currency, "value": amount},
        }
        if capture_id is not None:
            capture["id"] = capture_id
        captures = [capture]

    return {
        "id": order_id,
        "status": status,
        "purchase_units": [
            {
                "reference_id": reference_id or plan_id,
                "custom_id": custom_id or plan_id,
                "amount": {"currency_code": "USD", "value": "24.99"},
                "payments": {"captures": captures},
            }
        ],
    }


def approved_order(order_id, plan_id, amount="24.99"):
    """What GET /orders/{id} returns once the buyer has approved."""
    return {
        "id": order_id,
        "status": "APPROVED",
        "purchase_units": [
            {
                "reference_id": plan_id,
                "custom_id": plan_id,
                "amount": {"currency_code": "USD", "value": amount},
            }
        ],
    }


def read_state(plan_id):
    with database.SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)
        payment = db.query(Payment).filter(Payment.plan_id == plan_id).first()
        return (
            plan.payment_status,
            payment.status,
            plan.paypal_capture_id,
            payment.paypal_capture_id,
        )
