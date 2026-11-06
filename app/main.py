from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import update

from app.ai_agent import AIUnavailableError, parse_intent
from app.catalog import CATALOG, list_products
from app.database import Base, SessionLocal, engine
from app.models import Payment, PurchasePlan
from app.paypal_client import (
    PayPalError,
    capture_paypal_order,
    create_paypal_order,
    get_access_token,
    get_paypal_order,
)
from app.policy import PolicyViolation, validate_purchase


# Create database tables if they do not already exist.
Base.metadata.create_all(bind=engine)


app = FastAPI(
    title="NEXTPAY",
    description="AI-assisted commerce agent with PayPal Sandbox checkout.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PlanRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=50)
    quantity: int = Field(ge=1, le=5)
    budget_limit: Decimal = Field(gt=0, le=Decimal("1000.00"))
    goal: str = Field(min_length=3, max_length=500)


class ApprovalRequest(BaseModel):
    approved: bool


class AgentRequest(BaseModel):
    message: str = Field(min_length=10, max_length=1000)


def serialize_plan(plan: PurchasePlan) -> dict:
    """Convert a database plan into the existing API response structure."""
    return {
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "purchase": {
            "product_id": plan.product_id,
            "product_name": plan.product_name,
            "quantity": plan.quantity,
            "unit_price": plan.unit_price,
            "total": plan.total,
            "budget_limit": plan.budget_limit,
        },
        "status": plan.status,
        "payment_status": plan.payment_status,
        "paypal_order_id": plan.paypal_order_id,
        "paypal_capture_id": plan.paypal_capture_id,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "project": "NEXTPAY",
        "milestone": 6,
        "database": "sqlite",
    }


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

    plan = PurchasePlan(
        plan_id=str(uuid4()),
        goal=request.goal,
        product_id=purchase["product_id"],
        product_name=purchase["product_name"],
        quantity=request.quantity,
        unit_price=str(purchase["unit_price"]),
        total=str(purchase["total"]),
        budget_limit=str(request.budget_limit),
        status="awaiting_user_approval",
        payment_status="not_started",
    )

    with SessionLocal() as db:
        db.add(plan)
        db.commit()
        db.refresh(plan)

        response = serialize_plan(plan)
        response["message"] = (
            "Review the item, quantity, and total. "
            "No payment has been created."
        )
        return response


@app.post("/api/plans/{plan_id}/approval")
def approve_plan(plan_id: str, request: ApprovalRequest):
    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)

        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")

        if plan.status != "awaiting_user_approval":
            raise HTTPException(
                status_code=409,
                detail="This plan is no longer awaiting approval.",
            )

        if not request.approved:
            plan.status = "rejected_by_user"
            db.commit()

            response = serialize_plan(plan)
            response["message"] = "The user rejected this purchase."
            return response

        try:
            recalculated = validate_purchase(
                product_id=plan.product_id,
                quantity=plan.quantity,
                budget_limit=Decimal(plan.budget_limit),
            )
        except PolicyViolation as exc:
            plan.status = "blocked_by_policy"
            db.commit()
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        plan.product_name = recalculated["product_name"]
        plan.unit_price = str(recalculated["unit_price"])
        plan.total = str(recalculated["total"])
        plan.status = "approved_by_user"

        db.commit()
        db.refresh(plan)

        response = serialize_plan(plan)
        response["message"] = (
            "User approval recorded. "
            "PayPal checkout can now be started."
        )
        return response


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)

        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")

        return serialize_plan(plan)


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
    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)

        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")

        if plan.status != "approved_by_user":
            raise HTTPException(
                status_code=403,
                detail="User approval is required before creating a PayPal order.",
            )

        if plan.payment_status != "not_started":
            raise HTTPException(
                status_code=409,
                detail="Payment has already been started or completed.",
            )

        try:
            validated = validate_purchase(
                product_id=plan.product_id,
                quantity=plan.quantity,
                budget_limit=Decimal(plan.budget_limit),
            )

            result = await create_paypal_order(
                amount=str(validated["total"]),
                description=validated["product_name"],
                plan_id=plan_id,
            )

            plan.paypal_order_id = result["paypal_order_id"]
            plan.payment_status = "awaiting_paypal_approval"

            payment = Payment(
                payment_id=str(uuid4()),
                plan_id=plan_id,
                paypal_order_id=result["paypal_order_id"],
                amount=str(validated["total"]),
                currency="USD",
                status="awaiting_paypal_approval",
            )

            db.add(payment)
            db.commit()

            return {
                "success": True,
                "plan_id": plan_id,
                "paypal_order_id": result["paypal_order_id"],
                "paypal_status": result["status"],
                "payment_status": plan.payment_status,
                "approval_url": result["approval_url"],
                "message": (
                    "Open the PayPal URL to review and approve your purchase."
                ),
            }

        except PolicyViolation as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PayPalError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

def claim_paypal_capture(plan_id: str, order_id: str) -> bool:
    with SessionLocal() as db:
        plan_result = db.execute(
            update(PurchasePlan)
            .where(
                PurchasePlan.plan_id == plan_id,
                PurchasePlan.paypal_order_id == order_id,
                PurchasePlan.payment_status == "awaiting_paypal_approval",
            )
            .values(payment_status="capturing")
        )

        payment_result = db.execute(
            update(Payment)
            .where(
                Payment.plan_id == plan_id,
                Payment.paypal_order_id == order_id,
                Payment.status == "awaiting_paypal_approval",
            )
            .values(status="capturing")
        )

        if plan_result.rowcount != 1 or payment_result.rowcount != 1:
            db.rollback()
            return False

        db.commit()
        return True
@app.get("/api/paypal/return")
async def paypal_return(
    token: str,
    payer_id: str = Query(alias="PayerID"),
):
    frontend_url = "http://localhost:5173"

    with SessionLocal() as db:
        plan = (
            db.query(PurchasePlan)
            .filter(PurchasePlan.paypal_order_id == token)
            .first()
        )

        if plan is None:
            return RedirectResponse(
                f"{frontend_url}/payment/failure?reason=plan_not_found",
                status_code=303,
            )

        plan_id = plan.plan_id

        payment = (
            db.query(Payment)
            .filter(Payment.paypal_order_id == token)
            .first()
        )

        if payment is None or payment.plan_id != plan_id:
            return RedirectResponse(
                f"{frontend_url}/payment/failure?plan_id={plan_id}&reason=payment_record_not_found",
                status_code=303,
            )

        if plan.payment_status == "completed" and payment.status == "completed":
            return RedirectResponse(
                f"{frontend_url}/payment/success?plan_id={plan_id}",
                status_code=303,
            )

        if (
            plan.payment_status != "awaiting_paypal_approval"
            or payment.status != "awaiting_paypal_approval"
        ):
            return RedirectResponse(
                f"{frontend_url}/payment/failure?plan_id={plan_id}&reason=invalid_status",
                status_code=303,
            )

        plan.payment_status = "capturing"
        payment.status = "capturing"
        db.commit()

        try:
            validated = validate_purchase(
                product_id=plan.product_id,
                quantity=plan.quantity,
                budget_limit=Decimal(plan.budget_limit),
            )

            result = await capture_paypal_order(token)

            # 1. Verify PayPal returned the exact order we requested.
            if result.get("id") != token:
                raise PayPalError("PayPal order ID verification failed.")

            # 2. Verify the order completed.
            if result.get("status") != "COMPLETED":
                raise PayPalError("PayPal order is not completed.")

            # 3. Verify the purchase unit belongs to this plan.
            purchase_units = result.get("purchase_units", [])

            if not isinstance(purchase_units, list) or len(purchase_units) != 1:
                raise PayPalError("Unexpected PayPal purchase unit structure.")

            unit = purchase_units[0]

            if (
                unit.get("reference_id") != plan_id
                or unit.get("custom_id") != plan_id
            ):
                raise PayPalError("PayPal purchase plan identity mismatch.")

            # 4. Find a completed capture within the verified purchase unit.
            captures = (
                unit.get("payments", {}).get("captures", [])
            )

            if not isinstance(captures, list):
                raise PayPalError("Invalid PayPal capture structure.")

            completed_captures = [
                item for item in captures
                if item.get("status") == "COMPLETED"
            ]

            if len(completed_captures) != 1:
                raise PayPalError("Expected exactly one completed capture.")

            capture = completed_captures[0]

            if not capture.get("id"):
                raise PayPalError("PayPal capture ID is missing.")

            # 5. Verify the captured amount against the server's calculation
            #    and the amount recorded in our payment ledger.
            amount = capture.get("amount", {})
            expected_total = str(validated["total"])

            if (
                amount.get("currency_code") != "USD"
                or amount.get("value") != expected_total
                or payment.amount != expected_total
                or payment.currency != "USD"
            ):
                raise PayPalError("PayPal capture amount verification failed.")

            # 6. Record verified payment.
            plan.payment_status = "completed"
            plan.paypal_capture_id = capture["id"]

            payment.status = "completed"
            payment.paypal_capture_id = capture["id"]

            db.commit()

            return RedirectResponse(
                f"{frontend_url}/payment/success?plan_id={plan_id}",
                status_code=303,
            )

        except (PayPalError, PolicyViolation, KeyError, TypeError, AttributeError):
            db.rollback()

            plan.payment_status = "review_required"
            payment.status = "review_required"

            db.commit()

            return RedirectResponse(
                f"{frontend_url}/payment/failure?plan_id={plan_id}&reason=verification_error",
                status_code=303,
            )


@app.get("/api/paypal/cancel")
async def paypal_cancel(plan_id: str):
    frontend_url = "http://localhost:5173"

    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)

        if plan and plan.payment_status == "awaiting_paypal_approval":
            payment = (
                db.query(Payment)
                .filter(Payment.plan_id == plan_id)
                .first()
            )

            plan.payment_status = "cancelled_by_buyer"

            if payment and payment.status == "awaiting_paypal_approval":
                payment.status = "cancelled_by_buyer"

            db.commit()

            return RedirectResponse(
                f"{frontend_url}/payment/cancelled?plan_id={plan_id}",
                status_code=303,
            )

        return RedirectResponse(
            f"{frontend_url}/payment/cancelled?plan_id={plan_id}",
            status_code=303,
        )
