from uuid import uuid4

import pytest

import app.main as main_module
from app.database import SessionLocal
from app.models import Payment, PurchasePlan
from tests.plan_client import PlanClient


client = PlanClient(main_module.app)


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

    async def fake_get_paypal_order(paypal_order_id):
        # The buyer has approved the order but it is not captured yet.
        assert paypal_order_id == order_id

        return {
            "id": order_id,
            "status": "APPROVED",
            "purchase_units": [
                {
                    "reference_id": plan_id,
                    "custom_id": plan_id,
                    "amount": {"currency_code": "USD", "value": "24.99"},
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
        "get_paypal_order",
        fake_get_paypal_order,
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
    assert f"plan_id={plan_id}" in response.headers["location"]

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
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "reference_id": plan_id,
                    "custom_id": plan_id,
                    "payments": {
                        "captures": [
                            {
                                "id": f"TEST-CAPTURE-{uuid4()}",
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
    async def fake_get_paypal_order(paypal_order_id):
        assert paypal_order_id == order_id

        return {"id": order_id, "status": "APPROVED"}

    monkeypatch.setattr(
        main_module,
        "get_paypal_order",
        fake_get_paypal_order,
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
    assert "reason=review_required" in response.headers["location"]

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


# ---------------------------------------------------------------------------
# Additional HTTP-level tests (PayPal is always mocked)
# ---------------------------------------------------------------------------


def start_checkout(monkeypatch, plan_id=None, amount="24.99"):
    """Create a PayPal order through the API and return (plan_id, order_id)."""
    plan_id = plan_id or create_approved_plan()
    order_id = f"TEST-ORDER-{uuid4()}"

    async def fake_create_paypal_order(**kwargs):
        return {
            "paypal_order_id": order_id,
            "status": "CREATED",
            "approval_url": "https://www.sandbox.paypal.com/checkoutnow",
        }

    monkeypatch.setattr(main_module, "create_paypal_order", fake_create_paypal_order)

    response = client.post(f"/api/plans/{plan_id}/paypal/create-order")
    assert response.status_code == 200, response.text
    return plan_id, order_id


def install_paypal(monkeypatch, plan_id, order_id, *, lookup=None, capture=None):
    capture_id = f"TEST-CAPTURE-{uuid4()}"
    calls = {"capture": 0}

    async def default_lookup(_order_id):
        return {"id": order_id, "status": "APPROVED"}

    async def default_capture(_order_id):
        calls["capture"] += 1
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
                                "amount": {"currency_code": "USD", "value": "24.99"},
                            }
                        ]
                    },
                }
            ],
        }

    monkeypatch.setattr(main_module, "get_paypal_order", lookup or default_lookup)
    monkeypatch.setattr(main_module, "capture_paypal_order", capture or default_capture)
    return capture_id, calls


def paypal_return(order_id):
    return client.get(
        "/api/paypal/return",
        params={"token": order_id, "PayerID": "TEST-PAYER"},
        follow_redirects=False,
    )


def ledger_state(plan_id):
    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)
        payment = db.query(Payment).filter(Payment.plan_id == plan_id).first()
        return plan.payment_status, payment.status


def test_create_order_requires_approval():
    created = client.post(
        "/api/plans",
        json={
            "product_id": "desk_lamp",
            "quantity": 1,
            "budget_limit": "100.00",
            "goal": "Unapproved plan",
        },
    )
    plan_id = created.json()["plan_id"]

    response = client.post(f"/api/plans/{plan_id}/paypal/create-order")

    assert response.status_code == 403


def test_create_order_for_unknown_plan_returns_404():
    assert client.post("/api/plans/nope/paypal/create-order").status_code == 404


def test_second_create_order_is_rejected(monkeypatch):
    plan_id, _ = start_checkout(monkeypatch)

    again = client.post(f"/api/plans/{plan_id}/paypal/create-order")

    assert again.status_code == 409


def test_paypal_outage_while_creating_order_returns_503(monkeypatch):
    from app.paypal_client import PayPalUnavailableError

    plan_id = create_approved_plan()

    async def failing(**kwargs):
        raise PayPalUnavailableError("PayPal timed out while creating the order.")

    monkeypatch.setattr(main_module, "create_paypal_order", failing)

    response = client.post(f"/api/plans/{plan_id}/paypal/create-order")

    assert response.status_code == 503
    assert client.get(f"/api/plans/{plan_id}").json()["payment_status"] == "not_started"


def test_duplicate_return_callback_after_success_is_idempotent(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    capture_id, calls = install_paypal(monkeypatch, plan_id, order_id)

    first = paypal_return(order_id)
    second = paypal_return(order_id)

    assert "/payment/success" in first.headers["location"]
    assert "/payment/success" in second.headers["location"]
    assert calls["capture"] == 1
    assert ledger_state(plan_id) == ("completed", "completed")


def test_network_timeout_during_capture_is_not_reported_as_failure(monkeypatch):
    from app.paypal_client import PayPalUnavailableError

    plan_id, order_id = start_checkout(monkeypatch)

    async def timing_out(_order_id):
        raise PayPalUnavailableError("PayPal timed out while capturing the payment.")

    install_paypal(monkeypatch, plan_id, order_id, capture=timing_out)

    response = paypal_return(order_id)

    assert "/payment/pending" in response.headers["location"]
    assert "/payment/failure" not in response.headers["location"]
    assert ledger_state(plan_id) == ("capturing", "capturing")


def test_order_lookup_failure_leaves_payment_recoverable(monkeypatch):
    from app.paypal_client import PayPalUnavailableError

    plan_id, order_id = start_checkout(monkeypatch)

    async def failing_lookup(_order_id):
        raise PayPalUnavailableError("PayPal is temporarily unavailable.")

    _, calls = install_paypal(monkeypatch, plan_id, order_id, lookup=failing_lookup)

    response = paypal_return(order_id)

    assert "/payment/pending" in response.headers["location"]
    assert calls["capture"] == 0
    # Nothing was claimed, so the payment can be retried immediately.
    assert ledger_state(plan_id) == (
        "awaiting_paypal_approval",
        "awaiting_paypal_approval",
    )


def test_unexpected_server_error_in_callback_redirects_to_pending(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)

    async def exploding(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(main_module.payments, "process_paypal_return", exploding)

    response = paypal_return(order_id)

    assert response.status_code == 303
    assert "/payment/pending" in response.headers["location"]


def test_unknown_order_token_redirects_to_failure_without_changes(monkeypatch):
    response = paypal_return("NO-SUCH-ORDER")

    assert response.status_code == 303
    assert "/payment/failure" in response.headers["location"]


def test_unapproved_order_is_not_captured_and_can_be_retried(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)

    async def not_approved(_order_id):
        return {"id": order_id, "status": "CREATED"}

    _, calls = install_paypal(monkeypatch, plan_id, order_id, lookup=not_approved)

    response = paypal_return(order_id)

    assert "/payment/failure" in response.headers["location"]
    assert calls["capture"] == 0
    assert ledger_state(plan_id) == (
        "awaiting_paypal_approval",
        "awaiting_paypal_approval",
    )


def test_cancel_with_wrong_order_token_does_not_cancel(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)

    response = client.get(
        "/api/paypal/cancel",
        params={"plan_id": plan_id, "token": "SOMEONE-ELSES-ORDER"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert ledger_state(plan_id) == (
        "awaiting_paypal_approval",
        "awaiting_paypal_approval",
    )


def test_cancel_after_completion_does_not_show_cancelled(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    install_paypal(monkeypatch, plan_id, order_id)
    paypal_return(order_id)

    response = client.get(
        "/api/paypal/cancel",
        params={"plan_id": plan_id, "token": order_id},
        follow_redirects=False,
    )

    assert "/payment/success" in response.headers["location"]
    assert ledger_state(plan_id) == ("completed", "completed")


def test_reconcile_endpoint_recovers_an_abandoned_capture(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    install_paypal(monkeypatch, plan_id, order_id)

    response = client.post(f"/api/plans/{plan_id}/paypal/reconcile")

    assert response.status_code == 200
    assert response.json()["outcome"] == "success"
    assert response.json()["payment_status"] == "completed"


def test_reconcile_unknown_plan_returns_404():
    assert client.post("/api/plans/nope/paypal/reconcile").status_code == 404


def test_payment_ledger_endpoint_returns_audit_trail(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    install_paypal(monkeypatch, plan_id, order_id)
    paypal_return(order_id)

    body = client.get(f"/api/plans/{plan_id}/payment").json()

    assert body["payment_status"] == "completed"
    assert body["payments"][0]["status"] == "completed"
    assert [e["event_type"] for e in body["events"]][-1] == "capture_completed"
    assert client.get("/api/plans/nope/payment").status_code == 404


def test_cors_allows_only_the_configured_frontend():
    allowed = client.options(
        "/api/plans",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    blocked = client.options(
        "/api/plans",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in blocked.headers


def test_api_responses_are_not_cached_and_have_nosniff():
    response = client.get("/api/catalog")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_client_cannot_approve_a_plan_with_tampered_price():
    created = client.post(
        "/api/plans",
        json={
            "product_id": "desk_lamp",
            "quantity": 1,
            "budget_limit": "100.00",
            "goal": "Price tampering",
            "unit_price": "0.01",
            "total": "0.01",
        },
    )

    assert created.status_code == 201
    assert created.json()["purchase"]["total"] == "24.99"


# ---------------------------------------------------------------------------
# Cancellation / capture race (HTTP level). Service-level equivalents run in
# tests/test_payment_cancel_race.py.
# ---------------------------------------------------------------------------


def test_cancel_with_token_cancels_and_shows_cancelled_screen(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)

    response = client.get(
        "/api/paypal/cancel",
        params={"plan_id": plan_id, "token": order_id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/payment/cancelled" in response.headers["location"]
    assert ledger_state(plan_id) == ("cancelled_by_buyer", "cancelled_by_buyer")


def test_duplicate_cancel_callback_is_idempotent(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    params = {"plan_id": plan_id, "token": order_id}

    first = client.get("/api/paypal/cancel", params=params, follow_redirects=False)
    second = client.get("/api/paypal/cancel", params=params, follow_redirects=False)

    assert "/payment/cancelled" in first.headers["location"]
    assert "/payment/cancelled" in second.headers["location"]
    assert ledger_state(plan_id) == ("cancelled_by_buyer", "cancelled_by_buyer")


def test_cancel_while_capture_is_in_progress_never_cancels(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    main_module.payments.apply_transition(
        plan_id,
        order_id,
        main_module.payments.AWAITING,
        main_module.payments.CAPTURING,
        event_type="capture_claimed",
    )

    response = client.get(
        "/api/paypal/cancel",
        params={"plan_id": plan_id, "token": order_id},
        follow_redirects=False,
    )

    location = response.headers["location"]
    assert "/payment/pending" in location
    assert "reason=capture_in_progress" in location
    assert "/payment/cancelled" not in location
    assert ledger_state(plan_id) == ("capturing", "capturing")


def test_return_callback_after_cancellation_does_not_capture(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    _, calls = install_paypal(monkeypatch, plan_id, order_id)

    client.get(
        "/api/paypal/cancel",
        params={"plan_id": plan_id, "token": order_id},
        follow_redirects=False,
    )
    response = paypal_return(order_id)

    assert "/payment/cancelled" in response.headers["location"]
    assert calls["capture"] == 0
    assert ledger_state(plan_id) == ("cancelled_by_buyer", "cancelled_by_buyer")


def test_return_for_an_unapproved_order_leaves_it_cancellable(monkeypatch):
    """The original race: a return callback for an order the buyer did not
    approve must not leave the payment in `capturing`."""
    plan_id, order_id = start_checkout(monkeypatch)

    async def not_approved(_order_id):
        return {"id": order_id, "status": "CREATED"}

    install_paypal(monkeypatch, plan_id, order_id, lookup=not_approved)

    paypal_return(order_id)
    assert ledger_state(plan_id) == (
        "awaiting_paypal_approval",
        "awaiting_paypal_approval",
    )

    response = client.get(
        "/api/paypal/cancel",
        params={"plan_id": plan_id, "token": order_id},
        follow_redirects=False,
    )

    assert "/payment/cancelled" in response.headers["location"]
    assert ledger_state(plan_id) == ("cancelled_by_buyer", "cancelled_by_buyer")


def test_plan_endpoint_reports_verified_payment_only_after_capture(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    install_paypal(monkeypatch, plan_id, order_id)

    before = client.get(f"/api/plans/{plan_id}").json()
    assert before["payment_status"] == "awaiting_paypal_approval"
    assert before["payment_verified"] is False
    assert before["paypal_capture_id"] is None

    paypal_return(order_id)

    after = client.get(f"/api/plans/{plan_id}").json()
    assert after["payment_status"] == "completed"
    assert after["payment_verified"] is True
    assert after["paypal_capture_id"]


def test_plan_endpoint_never_verifies_a_review_required_payment(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)

    async def wrong_amount_capture(_order_id):
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
                                "id": f"TEST-CAPTURE-{uuid4()}",
                                "status": "COMPLETED",
                                "amount": {"currency_code": "USD", "value": "1.00"},
                            }
                        ]
                    },
                }
            ],
        }

    install_paypal(monkeypatch, plan_id, order_id, capture=wrong_amount_capture)
    paypal_return(order_id)

    body = client.get(f"/api/plans/{plan_id}").json()
    assert body["payment_status"] == "review_required"
    assert body["payment_verified"] is False


def test_plan_endpoint_responses_are_not_cacheable(monkeypatch):
    plan_id, _ = start_checkout(monkeypatch)

    response = client.get(f"/api/plans/{plan_id}")

    assert response.headers["cache-control"] == "no-store"


def test_reconcile_response_carries_the_verified_flag(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    install_paypal(monkeypatch, plan_id, order_id)

    body = client.post(f"/api/plans/{plan_id}/paypal/reconcile").json()

    assert body["outcome"] == "success"
    assert body["payment_verified"] is True


def test_reconcile_never_resurrects_a_cancelled_checkout(monkeypatch):
    plan_id, order_id = start_checkout(monkeypatch)
    _, calls = install_paypal(monkeypatch, plan_id, order_id)
    client.get(
        "/api/paypal/cancel",
        params={"plan_id": plan_id, "token": order_id},
        follow_redirects=False,
    )

    body = client.post(f"/api/plans/{plan_id}/paypal/reconcile").json()

    assert body["outcome"] == "cancelled"
    assert body["payment_verified"] is False
    assert calls["capture"] == 0


def test_amount_mismatch_redirects_with_a_generic_reason_but_keeps_the_detail(monkeypatch):
    """Public URL says `review_required`; the specific check that failed is
    kept in the audit trail only."""
    plan_id, order_id = start_checkout(monkeypatch)

    async def wrong_amount_capture(_order_id):
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
                                "id": f"TEST-CAPTURE-{uuid4()}",
                                "status": "COMPLETED",
                                "amount": {"currency_code": "USD", "value": "1.00"},
                            }
                        ]
                    },
                }
            ],
        }

    install_paypal(monkeypatch, plan_id, order_id, capture=wrong_amount_capture)

    response = paypal_return(order_id)
    location = response.headers["location"]

    assert "/payment/failure" in location
    assert "reason=review_required" in location
    assert "amount_mismatch" not in location
    assert ledger_state(plan_id) == ("review_required", "review_required")

    events = client.get(f"/api/plans/{plan_id}/payment").json()["events"]
    assert any(event["detail"] == "amount_mismatch" for event in events)
