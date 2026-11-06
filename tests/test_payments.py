from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.database import SessionLocal
from app.models import Payment, PurchasePlan


client = TestClient(main_module.app)


def create_approved_plan():
    response = client.post(
        "/api/plans",
        json={
            "product_id": "desk_lamp",
            "quantity": 1,
            "budget_limit": "100.00",
            "goal": "Test payment ledger persistence",
        },
    )

    assert response.status_code == 201
    plan_id = response.json()["plan_id"]

    approval = client.post(
        f"/api/plans/{plan_id}/approval",
        json={"approved": True},
    )

    assert approval.status_code == 200
    return plan_id


def test_paypal_order_is_saved_in_payment_ledger(monkeypatch):
    plan_id = create_approved_plan()

    order_id = f"TEST-ORDER-{uuid4()}"

    async def fake_create_paypal_order(**kwargs):
        assert kwargs["plan_id"] == plan_id
        assert kwargs["amount"] == "24.99"

        return {
            "paypal_order_id": order_id,
            "status": "CREATED",
            "approval_url": "https://example.com/approve",
        }

    monkeypatch.setattr(
        main_module,
        "create_paypal_order",
        fake_create_paypal_order,
    )

    response = client.post(f"/api/plans/{plan_id}/paypal/create-order")

    assert response.status_code == 200
    assert response.json()["payment_status"] == "awaiting_paypal_approval"

    with SessionLocal() as db:
        payment = (
            db.query(Payment)
            .filter(Payment.paypal_order_id == order_id)
            .first()
        )

        assert payment is not None
        assert payment.plan_id == plan_id
        assert payment.amount == "24.99"
        assert payment.currency == "USD"
        assert payment.status == "awaiting_paypal_approval"


def test_paypal_cancellation_updates_payment_ledger(monkeypatch):
    plan_id = create_approved_plan()
    order_id = f"TEST-ORDER-{uuid4()}"

    async def fake_create_paypal_order(**kwargs):
        return {
            "paypal_order_id": order_id,
            "status": "CREATED",
            "approval_url": "https://example.com/approve",
        }

    monkeypatch.setattr(
        main_module,
        "create_paypal_order",
        fake_create_paypal_order,
    )

    created = client.post(f"/api/plans/{plan_id}/paypal/create-order")

    assert created.status_code == 200

    cancelled = client.get(
        f"/api/paypal/cancel?plan_id={plan_id}",
        follow_redirects=False,
    )

    assert cancelled.status_code == 303
    assert "/payment/cancelled" in cancelled.headers["location"]

    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)
        payment = (
            db.query(Payment)
            .filter(Payment.plan_id == plan_id)
            .first()
        )

        assert plan.payment_status == "cancelled_by_buyer"
        assert payment.status == "cancelled_by_buyer"


def test_successful_paypal_capture_updates_ledger(monkeypatch):
    plan_id = create_approved_plan()
    order_id = f"TEST-CAPTURE-ORDER-{uuid4()}"
    capture_id = f"TEST-CAPTURE-{uuid4()}"

    async def fake_create_paypal_order(**kwargs):
        return {
            "paypal_order_id": order_id,
            "status": "CREATED",
            "approval_url": "https://example.com/approve",
        }

    async def fake_capture_paypal_order(paypal_order_id):
        assert paypal_order_id == order_id

        return {
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "reference_id": plan_id,
                    "custom_id": plan_id,
                    "payments": {
                        "captures": [
                            {
                                "id": capture_id,
                                "status": "COMPLETED",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "24.99",
                                },
                            }
                        ]
                    },
                }
            ],
        }

    monkeypatch.setattr(
        main_module,
        "create_paypal_order",
        fake_create_paypal_order,
    )
    monkeypatch.setattr(
        main_module,
        "capture_paypal_order",
        fake_capture_paypal_order,
    )

    created = client.post(f"/api/plans/{plan_id}/paypal/create-order")
    assert created.status_code == 200

    response = client.get(
        "/api/paypal/return",
        params={
            "token": order_id,
            "PayerID": "TEST-PAYER",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/payment/success" in response.headers["location"]

    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)
        payment = (
            db.query(Payment)
            .filter(Payment.paypal_order_id == order_id)
            .first()
        )

        assert plan.payment_status == "completed"
        assert plan.paypal_capture_id == capture_id
        assert payment.status == "completed"
        assert payment.paypal_capture_id == capture_id


def test_capture_amount_mismatch_requires_review(monkeypatch):
    plan_id = create_approved_plan()
    order_id = f"TEST-CAPTURE-ORDER-{uuid4()}"

    async def fake_create_paypal_order(**kwargs):
        return {
            "paypal_order_id": order_id,
            "status": "CREATED",
            "approval_url": "https://example.com/approve",
        }

    async def fake_capture_paypal_order(paypal_order_id):
        assert paypal_order_id == order_id

        return {
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-002",
                                "status": "COMPLETED",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "19.99",
                                },
                            }
                        ]
                    }
                }
            ],
        }

    monkeypatch.setattr(
        main_module,
        "create_paypal_order",
        fake_create_paypal_order,
    )
    monkeypatch.setattr(
        main_module,
        "capture_paypal_order",
        fake_capture_paypal_order,
    )

    created = client.post(f"/api/plans/{plan_id}/paypal/create-order")
    assert created.status_code == 200

    response = client.get(
        "/api/paypal/return",
        params={
            "token": order_id,
            "PayerID": "TEST-PAYER",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "payment/failure" in response.headers["location"]

    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)
        payment = (
            db.query(Payment)
            .filter(Payment.paypal_order_id == order_id)
            .first()
        )

        assert plan.payment_status == "review_required"
        assert payment.status == "review_required"
        assert plan.paypal_capture_id is None
