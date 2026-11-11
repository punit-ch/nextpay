"""Where the buyer's browser is sent after a PayPal callback.

Framework-free so it can be unit-tested. Redirects are always a fixed path on
the configured FRONTEND_URL; nothing from the request chooses the target.
"""

from urllib.parse import urlencode

from app import config
from app.payments import ReturnOutcome

OUTCOME_ROUTES = {
    "success": "/payment/success",
    "processing": "/payment/pending",
    "pending": "/payment/pending",
    "cancelled": "/payment/cancelled",
}
FAILURE_ROUTE = "/payment/failure"

# Detailed internal reason codes (amount_mismatch, ownership_mismatch,
# status_mismatch, capture_id_conflict, ...) stay in the server log and the
# payment_events audit table. The browser only learns that the payment needs
# review, which also avoids telling anyone which verification check tripped.
PUBLIC_REASON_FOR_RESULT = {"review_required": "review_required"}


def public_reason(outcome: ReturnOutcome) -> str:
    return PUBLIC_REASON_FOR_RESULT.get(
        outcome.result, outcome.reason or outcome.result
    )


def frontend_redirect_url(outcome: ReturnOutcome) -> str:
    path = OUTCOME_ROUTES.get(outcome.result, FAILURE_ROUTE)

    params = {}
    if outcome.plan_id:
        params["plan_id"] = outcome.plan_id

    if path in (FAILURE_ROUTE, OUTCOME_ROUTES["pending"]):
        params["reason"] = public_reason(outcome)

    query = f"?{urlencode(params)}" if params else ""

    return f"{config.frontend_url()}{path}{query}"
