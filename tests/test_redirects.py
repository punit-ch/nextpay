"""Public redirect behaviour after PayPal callbacks (no FastAPI needed)."""

from urllib.parse import parse_qs, urlsplit

import pytest

from app import database, payments
from app.models import PaymentEvent
from app.redirects import frontend_redirect_url, public_reason
from tests.test_payment_service import read_state, run_return, setup_payment

FRONTEND = "http://localhost:5173"


def parts(url):
    split = urlsplit(url)
    return split.path, {k: v[0] for k, v in parse_qs(split.query).items()}


def test_amount_mismatch_is_review_publicly_but_detailed_internally():
    """The reported failure: the internal code leaked into the redirect URL."""
    plan_id, order_id, fake = setup_payment()
    fake.completed_overrides = {"amount": "19.99"}

    outcome = run_return(fake)

    # Verification is NOT weakened: the mismatch is detected and recorded...
    assert outcome.result == "review_required"
    assert outcome.reason == "amount_mismatch"
    assert read_state(plan_id) == ("review_required", "review_required", None, None)
    with database.SessionLocal() as db:
        details = [e.detail for e in db.query(PaymentEvent).filter(PaymentEvent.plan_id == plan_id)]
    assert "amount_mismatch" in details

    # ...but the browser is only told the payment needs review.
    url = frontend_redirect_url(outcome)
    path, query = parts(url)
    assert url.startswith(FRONTEND)
    assert path == "/payment/failure"
    assert query == {"plan_id": plan_id, "reason": "review_required"}
    assert "amount_mismatch" not in url


@pytest.mark.parametrize(
    "internal_code",
    [
        "amount_mismatch",
        "currency_mismatch",
        "reference_id_mismatch",
        "custom_id_mismatch",
        "order_id_mismatch",
        "missing_capture_id",
        "multiple_completed_captures",
        "no_completed_capture",
        "purchase_unit_count",
        "ownership_mismatch",
        "ledger_amount_mismatch",
        "budget_exceeded",
        "capture_id_conflict",
        "status_mismatch",
    ],
)
def test_no_internal_review_code_reaches_the_browser(internal_code):
    outcome = payments.ReturnOutcome("review_required", "plan-1", internal_code)

    url = frontend_redirect_url(outcome)

    assert parts(url) == ("/payment/failure", {"plan_id": "plan-1", "reason": "review_required"})
    assert internal_code not in url


@pytest.mark.parametrize(
    "result, route",
    [
        ("success", "/payment/success"),
        ("cancelled", "/payment/cancelled"),
        ("processing", "/payment/pending"),
        ("pending", "/payment/pending"),
        ("failed", "/payment/failure"),
        ("not_approved", "/payment/failure"),
        ("not_found", "/payment/failure"),
        ("invalid_state", "/payment/failure"),
        ("review_required", "/payment/failure"),
        ("something_unexpected", "/payment/failure"),  # unknown never means success
    ],
)
def test_routes(result, route):
    path, _ = parts(frontend_redirect_url(payments.ReturnOutcome(result, "plan-1", "x")))

    assert path == route


def test_actionable_public_reasons_are_kept():
    assert parts(frontend_redirect_url(payments.ReturnOutcome("failed", "p", "declined")))[1]["reason"] == "declined"
    assert parts(frontend_redirect_url(payments.ReturnOutcome("pending", "p", "paypal_unavailable")))[1]["reason"] == "paypal_unavailable"
    assert parts(frontend_redirect_url(payments.ReturnOutcome("processing", "p", "capture_in_progress")))[1]["reason"] == "capture_in_progress"
    assert parts(frontend_redirect_url(payments.ReturnOutcome("pending", "p", "cancel_conflict")))[1]["reason"] == "cancel_conflict"


def test_success_and_cancelled_carry_only_the_plan_id():
    assert parts(frontend_redirect_url(payments.ReturnOutcome("success", "p", "completed")))[1] == {"plan_id": "p"}
    assert parts(frontend_redirect_url(payments.ReturnOutcome("cancelled", "p", "cancelled_by_buyer")))[1] == {"plan_id": "p"}


def test_missing_reason_falls_back_to_the_result():
    assert public_reason(payments.ReturnOutcome("failed", "p", None)) == "failed"


@pytest.mark.parametrize("hostile", ["a&reason=x#frag", "../../evil", "x\r\nLocation: http://evil", "%0d%0a", "http://evil.example"])
def test_plan_id_cannot_change_the_redirect_target_or_inject_parameters(hostile):
    url = frontend_redirect_url(payments.ReturnOutcome("success", hostile, "completed"))

    split = urlsplit(url)
    assert url.startswith(FRONTEND + "/payment/success?")
    assert split.netloc == "localhost:5173"
    assert split.fragment == ""
    assert "\r" not in url and "\n" not in url
    assert parse_qs(split.query) == {"plan_id": [hostile]}  # exactly one parameter, intact


def test_frontend_origin_comes_from_configuration(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://nextpay.example.app/")

    url = frontend_redirect_url(payments.ReturnOutcome("success", "p", "completed"))

    assert url == "https://nextpay.example.app/payment/success?plan_id=p"
