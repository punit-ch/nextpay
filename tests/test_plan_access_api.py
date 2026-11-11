"""Plan access protection at the HTTP level (needs FastAPI).

`authed` behaves like the frontend (remembers and sends the plan token);
`bare` is a caller with no token at all.
"""

from uuid import uuid4

import app.main as main_module
from app import plan_access
from app.database import SessionLocal
from app.models import Payment, PurchasePlan
from fastapi.testclient import TestClient
from tests.plan_client import PlanClient

authed = PlanClient(main_module.app)
bare = TestClient(main_module.app)

DENIED = "A valid plan access token is required."


def new_plan():
    response = authed.post(
        "/api/plans",
        json={
            "product_id": "desk_lamp",
            "quantity": 1,
            "budget_limit": "100.00",
            "goal": "Access test",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def approve(plan_id):
    response = authed.post(f"/api/plans/{plan_id}/approval", json={"approved": True})
    assert response.status_code == 200, response.text


def with_token(token):
    return {"X-Plan-Token": token}


def test_creating_a_plan_issues_the_token_once():
    created = new_plan()

    assert isinstance(created["access_token"], str) and len(created["access_token"]) >= 40
    fetched = authed.get(f"/api/plans/{created['plan_id']}").json()
    assert "access_token" not in fetched
    assert "access_token_hash" not in fetched
    assert created["access_token"] not in authed.get(f"/api/plans/{created['plan_id']}/payment").text


def test_only_a_hash_of_the_token_is_stored():
    created = new_plan()

    with SessionLocal() as db:
        plan = db.get(PurchasePlan, created["plan_id"])
        assert plan.access_token_hash == plan_access.hash_token(created["access_token"])
        assert plan.access_token_hash != created["access_token"]


def test_each_plan_gets_a_different_token():
    assert new_plan()["access_token"] != new_plan()["access_token"]


def test_correct_token_reads_the_plan():
    created = new_plan()

    response = bare.get(f"/api/plans/{created['plan_id']}", headers=with_token(created["access_token"]))

    assert response.status_code == 200
    assert response.json()["plan_id"] == created["plan_id"]
    assert response.json()["payment_verified"] is False


def test_header_name_is_case_insensitive():
    created = new_plan()

    response = bare.get(
        f"/api/plans/{created['plan_id']}", headers={"x-plan-token": created["access_token"]}
    )

    assert response.status_code == 200


def test_missing_token_is_forbidden():
    created = new_plan()

    response = bare.get(f"/api/plans/{created['plan_id']}")

    assert response.status_code == 403
    assert response.json()["detail"] == DENIED


def test_wrong_token_is_forbidden_and_reveals_nothing():
    created = new_plan()

    response = bare.get(f"/api/plans/{created['plan_id']}", headers=with_token("not-the-token"))

    assert response.status_code == 403
    assert response.json() == {"detail": DENIED}
    assert created["access_token"] not in response.text


def test_another_plans_token_is_forbidden():
    first, second = new_plan(), new_plan()

    response = bare.get(f"/api/plans/{first['plan_id']}", headers=with_token(second["access_token"]))

    assert response.status_code == 403


def test_unknown_plan_is_404():
    response = bare.get(f"/api/plans/{uuid4()}", headers=with_token("anything"))

    assert response.status_code == 404


def test_every_plan_endpoint_refuses_a_caller_without_the_token():
    created = new_plan()
    pid = created["plan_id"]

    attempts = [
        bare.get(f"/api/plans/{pid}"),
        bare.get(f"/api/plans/{pid}/payment"),
        bare.post(f"/api/plans/{pid}/approval", json={"approved": True}),
        bare.post(f"/api/plans/{pid}/paypal/create-order"),
        bare.post(f"/api/plans/{pid}/paypal/reconcile"),
    ]

    assert [r.status_code for r in attempts] == [403] * 5


def test_a_refused_approval_changes_nothing():
    created = new_plan()
    pid = created["plan_id"]

    bare.post(f"/api/plans/{pid}/approval", json={"approved": True})
    bare.post(f"/api/plans/{pid}/approval", json={"approved": False}, headers=with_token("wrong"))

    assert authed.get(f"/api/plans/{pid}").json()["status"] == "awaiting_user_approval"


def test_a_refused_create_order_never_reaches_paypal(monkeypatch):
    created = new_plan()
    approve(created["plan_id"])
    calls = []

    async def spy(**kwargs):
        calls.append(kwargs)
        return {"paypal_order_id": "X", "status": "CREATED", "approval_url": "https://www.sandbox.paypal.com/x"}

    monkeypatch.setattr(main_module, "create_paypal_order", spy)

    response = bare.post(f"/api/plans/{created['plan_id']}/paypal/create-order")

    assert response.status_code == 403
    assert calls == []
    assert authed.get(f"/api/plans/{created['plan_id']}").json()["payment_status"] == "not_started"


def test_a_refused_reconcile_never_reaches_paypal(monkeypatch):
    created = new_plan()
    calls = []

    async def spy(_order_id):
        calls.append(_order_id)
        return {}

    monkeypatch.setattr(main_module, "get_paypal_order", spy)
    monkeypatch.setattr(main_module, "capture_paypal_order", spy)

    response = bare.post(f"/api/plans/{created['plan_id']}/paypal/reconcile")

    assert response.status_code == 403
    assert calls == []


def test_plans_created_before_the_upgrade_fail_closed():
    plan_id = str(uuid4())
    with SessionLocal() as db:
        db.add(
            PurchasePlan(
                plan_id=plan_id, goal="old", product_id="desk_lamp",
                product_name="Lamp", quantity=1, unit_price="24.99", total="24.99",
                budget_limit="100.00", status="approved_by_user", payment_status="not_started",
            )
        )
        db.commit()

    for token in (None, "", "None", "anything-at-all-1234567890"):
        headers = with_token(token) if token else {}
        assert bare.get(f"/api/plans/{plan_id}", headers=headers).status_code == 403


def test_paypal_redirects_work_without_a_token(monkeypatch):
    """PayPal's browser redirects cannot send custom headers."""
    created = new_plan()
    pid = created["plan_id"]
    approve(pid)
    order_id = f"TEST-ORDER-{uuid4()}"

    async def fake_create(**kwargs):
        return {"paypal_order_id": order_id, "status": "CREATED",
                "approval_url": "https://www.sandbox.paypal.com/checkoutnow"}

    monkeypatch.setattr(main_module, "create_paypal_order", fake_create)
    assert authed.post(f"/api/plans/{pid}/paypal/create-order").status_code == 200

    cancelled = bare.get(
        "/api/paypal/cancel", params={"plan_id": pid, "token": order_id}, follow_redirects=False
    )

    assert cancelled.status_code == 303
    assert "/payment/cancelled" in cancelled.headers["location"]
    assert authed.get(f"/api/plans/{pid}").json()["payment_status"] == "cancelled_by_buyer"


def test_return_callback_completes_a_payment_without_a_token(monkeypatch):
    created = new_plan()
    pid = created["plan_id"]
    approve(pid)
    order_id = f"TEST-ORDER-{uuid4()}"
    capture_id = f"TEST-CAPTURE-{uuid4()}"

    async def fake_create(**kwargs):
        return {"paypal_order_id": order_id, "status": "CREATED",
                "approval_url": "https://www.sandbox.paypal.com/checkoutnow"}

    async def lookup(_id):
        return {"id": order_id, "status": "APPROVED"}

    async def capture(_id):
        return {
            "id": order_id, "status": "COMPLETED",
            "purchase_units": [{
                "reference_id": pid, "custom_id": pid,
                "payments": {"captures": [{"id": capture_id, "status": "COMPLETED",
                                            "amount": {"currency_code": "USD", "value": "24.99"}}]},
            }],
        }

    monkeypatch.setattr(main_module, "create_paypal_order", fake_create)
    monkeypatch.setattr(main_module, "get_paypal_order", lookup)
    monkeypatch.setattr(main_module, "capture_paypal_order", capture)
    authed.post(f"/api/plans/{pid}/paypal/create-order")

    response = bare.get(
        "/api/paypal/return", params={"token": order_id, "PayerID": "P"}, follow_redirects=False
    )

    assert "/payment/success" in response.headers["location"]
    body = authed.get(f"/api/plans/{pid}").json()
    assert body["payment_status"] == "completed" and body["payment_verified"] is True
    # ...and the result page cannot be read by someone without the token.
    assert bare.get(f"/api/plans/{pid}").status_code == 403


def test_cors_preflight_allows_the_plan_token_header():
    response = bare.options(
        "/api/plans/some-plan",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-plan-token",
        },
    )

    assert response.status_code == 200
    assert "x-plan-token" in response.headers["access-control-allow-headers"].lower()


def test_cors_preflight_still_refuses_unknown_origins():
    response = bare.options(
        "/api/plans/some-plan",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-plan-token",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_a_payment_row_is_untouched_by_a_refused_request(monkeypatch):
    created = new_plan()
    pid = created["plan_id"]

    bare.post(f"/api/plans/{pid}/paypal/reconcile")

    with SessionLocal() as db:
        assert db.query(Payment).filter(Payment.plan_id == pid).count() == 0
