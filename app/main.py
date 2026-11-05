from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.catalog import list_products
from app.policy import PolicyViolation, validate_purchase

app = FastAPI(
    title="NEXTPAY",
    description=(
        "AI-assisted commerce agent. "
        "Milestone 1 uses sample catalogue data and no real payments."
    ),
    version="0.1.0",
)

# Temporary in-memory state for local development only.
# Replace with persistent storage before deploying.
plans: dict[str, dict] = {}


class PlanRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=50)
    quantity: int = Field(ge=1, le=5)
    budget_limit: Decimal = Field(gt=0, le=Decimal("1000.00"))
    goal: str = Field(min_length=3, max_length=500)


class ApprovalRequest(BaseModel):
    approved: bool


@app.get("/health")
def health():
    return {"status": "ok", "project": "NEXTPAY", "milestone": 1}


@app.get("/api/catalog")
def catalog():
    return {
        "source": "sample_data",
        "products": list_products(),
    }


@app.post("/api/plans", status_code=201)
def create_plan(request: PlanRequest):
    try:
        purchase = validate_purchase(
            product_id=request.product_id,
            quantity=request.quantity,
            budget_limit=request.budget_limit,
        )
    except PolicyViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    plan_id = str(uuid4())

    plan = {
        "plan_id": plan_id,
        "goal": request.goal,
        "purchase": purchase,
        "status": "awaiting_user_approval",
        "payment_status": "not_started",
        "message": (
            "Review the item, quantity, and total. "
            "No payment has been created."
        ),
    }

    plans[plan_id] = plan
    return plan


@app.post("/api/plans/{plan_id}/approval")
def approve_plan(plan_id: str, request: ApprovalRequest):
    plan = plans.get(plan_id)

    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    if plan["status"] != "awaiting_user_approval":
        raise HTTPException(
            status_code=409,
            detail="This plan is no longer awaiting approval.",
        )

    if not request.approved:
        plan["status"] = "rejected_by_user"
        plan["message"] = "The user rejected this purchase."
        return plan

    # Recalculate from trusted catalogue data at approval time.
    purchase = plan["purchase"]

    try:
        recalculated = validate_purchase(
            product_id=purchase["product_id"],
            quantity=purchase["quantity"],
            budget_limit=Decimal(purchase["budget_limit"]),
        )
    except PolicyViolation as exc:
        plan["status"] = "blocked_by_policy"
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    plan["purchase"] = recalculated
    plan["status"] = "approved_by_user"
    plan["message"] = (
        "User approval recorded. "
        "PayPal checkout is not implemented in Milestone 1."
    )
    return plan


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    plan = plans.get(plan_id)

    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    return plan