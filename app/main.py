from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.ai_agent import AIUnavailableError, parse_intent
from app.catalog import CATALOG, list_products
from app.policy import PolicyViolation, validate_purchase
from app.paypal_client import PayPalError, create_paypal_order, get_access_token

app = FastAPI(
    title="NEXTPAY",
    description=(
        "AI-assisted commerce agent. "
        "Milestone 1 uses sample catalogue data and no real payments."
    ),
    version="0.1.0",
)

plans: dict[str, dict] = {}


class PlanRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=50)
    quantity: int = Field(ge=1, le=5)
    budget_limit: Decimal = Field(gt=0, le=Decimal("1000.00"))
    goal: str = Field(min_length=3, max_length=500)


class ApprovalRequest(BaseModel):
    approved: bool


class AgentRequest(BaseModel):
    message: str = Field(min_length=10, max_length=1000)


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


@app.post("/api/agent/recommend")
def agent_recommend(request: AgentRequest):
    try:
        intent = parse_intent(request.message)
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if intent.clarification_needed or intent.budget_limit is None:
        return {
            "status": "needs_clarification",
            "intent": intent.model_dump(mode="json"),
            "question": (
                intent.clarification_question
                or "Please specify your maximum budget in USD."
            ),
            "recommendations": [],
            "payment_status": "not_started",
        }

    candidates = []
    for product in CATALOG.values():
        if intent.category not in ("any", product["category"]):
            continue
        if product["price"] > intent.budget_limit:
            continue

        candidates.append(
            {
                "product_id": product["id"],
                "name": product["name"],
                "price": str(product["price"]),
                "currency": "USD",
                "sample_data": True,
            }
        )

    candidates.sort(key=lambda item: Decimal(item["price"]))
    return {
        "status": "recommendations_ready",
        "intent": intent.model_dump(mode="json"),
        "recommendations": candidates,
        "next_step": (
            "Choose a product and submit it to POST /api/plans. "
            "User approval is required before checkout."
        ),
        "payment_status": "not_started",
    }


@app.get("/api/paypal/test-auth")
async def test_paypal_auth():
    try:
        token_data = await get_access_token()
        return {
            "success": True,
            "message": "Successfully authenticated with PayPal Sandbox.",
            "token_type": token_data["token_type"],
            "expires_in": token_data["expires_in"],
            "environment": "sandbox",
        }
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/plans/{plan_id}/paypal/create-order")
async def create_plan_paypal_order(plan_id: str):
    plan = plans.get(plan_id)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    if plan.get("status") != "approved_by_user":
        raise HTTPException(
            status_code=403,
            detail="User approval is required before creating a PayPal order.",
        )

    if plan.get("payment_status") != "not_started":
        raise HTTPException(
            status_code=409,
            detail="Payment has already been started or completed.",
        )

    try:
        purchase = plan.get("purchase", {})
        validated = validate_purchase(
            product_id=purchase["product_id"],
            quantity=purchase["quantity"],
            budget_limit=Decimal(str(purchase["budget_limit"])),
        )

        result = await create_paypal_order(
            amount=str(validated["total"]),
            description=validated["product_name"],
            plan_id=plan_id,
        )

        plan["paypal_order_id"] = result["paypal_order_id"]
        plan["payment_status"] = "awaiting_paypal_approval"

        return {
            "success": True,
            "plan_id": plan_id,
            "paypal_order_id": result["paypal_order_id"],
            "paypal_status": result["status"],
            "payment_status": plan["payment_status"],
            "approval_url": result["approval_url"],
            "message": "Open the PayPal URL to review and approve your purchase.",
        }
    except PolicyViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
