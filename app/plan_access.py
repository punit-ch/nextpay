"""Plan-level access protection.

Every purchase plan gets a random, unguessable access token when it is
created. The token is returned to the browser exactly once, in the response to
``POST /api/plans``; only its SHA-256 hash is stored. Every endpoint that
reads or changes a specific plan must present the token in the
``X-Plan-Token`` header.

Why a plain SHA-256 hash is enough here: the token is 256 bits of randomness,
so there is nothing to brute-force; hashing just means a leaked database does
not leak usable tokens. Comparison is constant-time.

Not covered on purpose: PayPal's browser redirects (``/api/paypal/return`` and
``/api/paypal/cancel``). A redirect cannot carry custom headers, and those
endpoints only ever act on data re-verified with PayPal.
"""

import hashlib
import hmac
import secrets

from app import database
from app.models import PurchasePlan

HEADER_NAME = "X-Plan-Token"
TOKEN_BYTES = 32


class PlanNotFound(Exception):
    status_code = 404


class PlanAccessDenied(Exception):
    status_code = 403


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str | None, stored_hash: str | None) -> bool:
    """True only for a non-empty token whose hash equals the stored hash.

    A plan without a stored hash (created before this protection existed)
    can never be matched: access fails closed.
    """
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(token), stored_hash)


def verify_plan_access(plan_id: str, token: str | None) -> None:
    """Raise PlanNotFound / PlanAccessDenied unless ``token`` unlocks the plan."""
    with database.SessionLocal() as db:
        row = (
            db.query(PurchasePlan.access_token_hash)
            .filter(PurchasePlan.plan_id == plan_id)
            .first()
        )

    if row is None:
        raise PlanNotFound("Plan not found.")

    if not token_matches(token, row[0]):
        raise PlanAccessDenied("A valid plan access token is required.")
