
from tests.plan_client import PlanClient

from app.main import app

client = PlanClient(app)


def make_plan(
    product_id="desk_lamp",
    quantity=1,
    budget_limit="100.00",
):
    return client.post(
        "/api/plans",
        json={
            "product_id": product_id,
            "quantity": quantity,
            "budget_limit": budget_limit,
            "goal": "Find a useful product within my budget",
        },
    )


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"


def test_catalog_is_marked_as_sample_data():
    response = client.get("/api/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "sample_data"
    assert len(body["products"]) >= 1
    assert all(p["sample_data"] for p in body["products"])


def test_valid_plan_uses_server_catalogue_price():
    response = make_plan()

    assert response.status_code == 201
    body = response.json()
    assert body["purchase"]["unit_price"] == "24.99"
    assert body["purchase"]["total"] == "24.99"
    assert body["status"] == "awaiting_user_approval"
    assert body["payment_status"] == "not_started"


def test_budget_exceeded_is_rejected():
    response = make_plan(
        product_id="headphones",
        quantity=2,
        budget_limit="100.00",
    )

    assert response.status_code == 422


def test_unknown_product_is_rejected():
    response = make_plan(product_id="premium_laptop")

    assert response.status_code == 422


def test_quantity_limit_is_enforced():
    response = make_plan(quantity=6)

    assert response.status_code == 422


def test_approval_must_be_explicit():
    plan_response = make_plan()
    plan_id = plan_response.json()["plan_id"]

    before = client.get(f"/api/plans/{plan_id}").json()
    assert before["status"] == "awaiting_user_approval"

    approval = client.post(
        f"/api/plans/{plan_id}/approval",
        json={"approved": False},
    )

    assert approval.status_code == 200
    assert approval.json()["status"] == "rejected_by_user"


def test_approved_plan_does_not_claim_payment():
    plan_response = make_plan()
    plan_id = plan_response.json()["plan_id"]

    approval = client.post(
        f"/api/plans/{plan_id}/approval",
        json={"approved": True},
    )

    assert approval.status_code == 200
    body = approval.json()

    assert body["status"] == "approved_by_user"
    assert body["payment_status"] == "not_started"
    assert "checkout can now be started" in body["message"].lower()


def test_plan_cannot_be_approved_twice():
    plan_response = make_plan()
    plan_id = plan_response.json()["plan_id"]

    first = client.post(
        f"/api/plans/{plan_id}/approval",
        json={"approved": True},
    )

    second = client.post(
        f"/api/plans/{plan_id}/approval",
        json={"approved": True},
    )

    assert first.status_code == 200
    assert second.status_code == 409


def test_missing_plan_returns_404():
    response = client.get("/api/plans/not-a-real-plan")

    assert response.status_code == 404


def test_plan_persists_in_database():
    created = make_plan()
    assert created.status_code == 201

    plan_id = created.json()["plan_id"]

    retrieved = client.get(f"/api/plans/{plan_id}")

    assert retrieved.status_code == 200
    body = retrieved.json()

    assert body["plan_id"] == plan_id
    assert body["purchase"]["product_id"] == "desk_lamp"
    assert body["payment_status"] == "not_started"


def test_approved_plan_is_persisted():
    created = make_plan()
    plan_id = created.json()["plan_id"]

    approval = client.post(
        f"/api/plans/{plan_id}/approval",
        json={"approved": True},
    )

    assert approval.status_code == 200

    retrieved = client.get(f"/api/plans/{plan_id}")

    assert retrieved.status_code == 200
    assert retrieved.json()["status"] == "approved_by_user"
