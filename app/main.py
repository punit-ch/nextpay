import hashlib
import hmac
import logging
import secrets
from decimal import Decimal
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import update

from app import config, payments
from app.ai_agent import AIUnavailableError, parse_intent
from app.catalog import CATALOG, list_products
from app.database import Base, SessionLocal, engine
from app.models import PlanAccess, PurchasePlan
from app.paypal_client import (
    PayPalError,
    PayPalUnavailableError,
    capture_paypal_order,
    create_paypal_order,
    get_access_token,
    get_paypal_order,
)
from app.policy import PolicyViolation, validate_purchase
from app.rate_limit import rate_limit_middleware

logger = logging.getLogger("nextpay.api")

# Create database tables if they do not already exist.
Base.metadata.create_all(bind=engine)


app = FastAPI(
    title="NEXTPAY",
    description="AI-assisted commerce agent with PayPal Sandbox checkout.",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Plan-Access-Token"],
)

app.middleware("http")(rate_limit_middleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _hash_plan_access_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_plan_access(
    plan_id: str,
    x_plan_access_token: str | None = Header(
        default=None,
        alias="X-Plan-Access-Token",
        min_length=32,
        max_length=128,
    ),
) -> None:
    """Authorize possession of the per-plan capability secret."""
    if not x_plan_access_token:
        raise HTTPException(
            status_code=401,
            detail="A valid plan access token is required.",
        )

    with SessionLocal() as db:
        access = db.get(PlanAccess, plan_id)
        if access is None or not hmac.compare_digest(
            access.token_hash,
            _hash_plan_access_token(x_plan_access_token),
        ):
            raise HTTPException(status_code=403, detail="Plan access denied.")


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
        "paypal_mode": "sandbox",
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

    plan_id = str(uuid4())
    plan_access_token = secrets.token_urlsafe(32)
    plan = PurchasePlan(
        plan_id=plan_id,
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
        # Flush the parent row before inserting its foreign-key child.
        db.flush()
        db.add(
            PlanAccess(
                plan_id=plan_id,
                token_hash=_hash_plan_access_token(plan_access_token),
            )
        )
        db.commit()
        db.refresh(plan)

        response = serialize_plan(plan)
        response["message"] = (
            "Review the item, quantity, and total. "
            "No payment has been created."
        )
        response["plan_access_token"] = plan_access_token
        return response


@app.post("/api/plans/{plan_id}/approval")
def approve_plan(
    plan_id: str,
    request: ApprovalRequest,
    _authorized: None = Depends(require_plan_access),
):
    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)

        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")

        if plan.status != "awaiting_user_approval":
            raise HTTPException(
                status_code=409,
                detail="This plan is no longer awaiting approval.",
            )

        def move_plan(new_status: str, **extra) -> bool:
            """Atomic status change; False if another request got there first."""
            result = db.execute(
                update(PurchasePlan)
                .where(
                    PurchasePlan.plan_id == plan_id,
                    PurchasePlan.status == "awaiting_user_approval",
                )
                .values(status=new_status, **extra)
            )
            if result.rowcount != 1:
                db.rollback()
                return False
            db.commit()
            return True

        def already_handled():
            return HTTPException(
                status_code=409,
                detail="This plan is no longer awaiting approval.",
            )

        if not request.approved:
            if not move_plan("rejected_by_user"):
                raise already_handled()

            db.refresh(plan)
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
            move_plan("blocked_by_policy")
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not move_plan(
            "approved_by_user",
            product_name=recalculated["product_name"],
            unit_price=str(recalculated["unit_price"]),
            total=str(recalculated["total"]),
        ):
            raise already_handled()

        db.refresh(plan)
        response = serialize_plan(plan)
        response["message"] = (
            "User approval recorded. "
            "PayPal checkout can now be started."
        )
        return response


@app.get("/api/plans/{plan_id}")
def get_plan(
    plan_id: str,
    _authorized: None = Depends(require_plan_access),
):
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
    user_query = request.message.casefold()

    for product in CATALOG.values():

        # Enforce category matching.
        if intent.category not in ("any", product["category"]):
            continue

        # Match the actual requested product.
        keywords = product.get("keywords", [])
        searchable_terms = [
            product["name"],
            *keywords,
        ]

        matches_query = any(
            term.casefold() in user_query
            for term in searchable_terms
        )

        if not matches_query:
            continue

        # Enforce budget.
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
    if config.is_production():
        raise HTTPException(status_code=404, detail="Not found.")

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


def _paypal_http_error(exc: PayPalError) -> HTTPException:
    status = 503 if isinstance(exc, PayPalUnavailableError) else 502
    return HTTPException(status_code=status, detail=str(exc))


@app.post("/api/plans/{plan_id}/paypal/create-order")
async def create_plan_paypal_order(
    plan_id: str,
    _authorized: None = Depends(require_plan_access),
):
    try:
        return await payments.create_order_for_plan(
            plan_id,
            create_order=create_paypal_order,
        )
    except payments.PaymentServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except PolicyViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PayPalError as exc:
        raise _paypal_http_error(exc) from exc


# Where each outcome sends the buyer in the frontend.
_OUTCOME_ROUTES = {
    "success": "/payment/success",
    "processing": "/payment/pending",
    "pending": "/payment/pending",
    "cancelled": "/payment/cancelled",
}


def _frontend_redirect(outcome: payments.ReturnOutcome) -> RedirectResponse:
    path = _OUTCOME_ROUTES.get(outcome.result, "/payment/failure")

    params = {}
    if outcome.plan_id:
        params["plan_id"] = outcome.plan_id

    if path in ("/payment/failure", "/payment/pending"):
        if outcome.result == "review_required":
            params["reason"] = "review_required"
        else:
            params["reason"] = outcome.reason or outcome.result

    query = f"?{urlencode(params)}" if params else ""

    # Only a fixed path on the configured frontend: no user-supplied redirect.
    return RedirectResponse(
        url=f"{config.frontend_url()}{path}{query}",
        status_code=303,
    )


@app.get("/api/paypal/return")
async def paypal_return(
    token: str = Query(max_length=256),
    payer_id: str | None = Query(default=None, alias="PayerID", max_length=64),
):
    """PayPal sends the buyer here after approval. Also safe to refresh."""
    try:
        outcome = await payments.process_paypal_return(
            token,
            get_order=get_paypal_order,
            capture_order=capture_paypal_order,
        )
    except Exception:
        # The payment state is unchanged or recoverable; never claim failure.
        logger.exception("Unhandled error in PayPal return callback")
        outcome = payments.ReturnOutcome("pending", None, "internal_error")

    return _frontend_redirect(outcome)


@app.get("/api/paypal/cancel")
async def paypal_cancel(
    plan_id: str = Query(max_length=64),
    token: str | None = Query(default=None, max_length=256),
):
    try:
        outcome = payments.cancel_checkout(plan_id, token)
    except Exception:
        logger.exception("Unhandled error in PayPal cancel callback")
        outcome = payments.ReturnOutcome("cancelled", plan_id, "internal_error")

    return _frontend_redirect(outcome)


@app.post("/api/plans/{plan_id}/paypal/reconcile")
async def reconcile_plan_payment(
    plan_id: str,
    _authorized: None = Depends(require_plan_access),
):
    """Re-check a plan's payment against PayPal (used by the 'check again' button)."""
    try:
        outcome = await payments.reconcile_plan(
            plan_id,
            get_order=get_paypal_order,
            capture_order=capture_paypal_order,
        )
    except payments.PaymentServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unhandled error while reconciling plan")
        raise HTTPException(
            status_code=500,
            detail="Could not check the payment right now. Please try again.",
        )

    with SessionLocal() as db:
        plan = db.get(PurchasePlan, plan_id)
        payment_status = plan.payment_status if plan else None

    return {
        "plan_id": plan_id,
        "outcome": outcome.result,
        "reason": outcome.reason,
        "payment_status": payment_status,
    }


@app.get("/api/plans/{plan_id}/payment")
def get_plan_payment(
    plan_id: str,
    _authorized: None = Depends(require_plan_access),
):
    """Payment ledger and audit trail for a plan."""
    ledger = payments.get_payment_ledger(plan_id)

    if ledger is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    return ledger
