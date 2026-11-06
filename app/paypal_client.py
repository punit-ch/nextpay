"""PayPal Sandbox REST client.

Error model (important for payment recovery):

* ``PayPalUnavailableError``  - network failure, timeout, HTTP 429/5xx.
  We cannot know whether PayPal processed the request.
* ``PayPalMalformedResponseError`` - PayPal answered but not with usable JSON.
  Also treated as "outcome unknown".
* ``PayPalAuthError`` / ``PayPalConfigError`` - credentials rejected or
  missing. The request was not processed.
* ``PayPalRejectedError`` - PayPal explicitly refused (other HTTP 4xx). The
  ``issue`` attribute carries PayPal's machine-readable reason, e.g.
  ``INSTRUMENT_DECLINED``.

Callers must only treat ``PayPalRejectedError`` as a definitive answer.

Only the Sandbox host is allowed. Credentials, access tokens and response
bodies are never logged.
"""

import logging
import os
import re
import uuid
from urllib.parse import urlencode, urlsplit

import httpx

from app import config

logger = logging.getLogger("nextpay.paypal")

_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PAYPAL_CODE_RE = re.compile(r"^[A-Z0-9_]{1,64}$")
_DEBUG_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Tests inject an ``httpx.MockTransport`` here. Never set in production.
_transport: httpx.AsyncBaseTransport | None = None


class PayPalError(Exception):
    """Base class. Messages are safe to show to API clients."""

    #: True when we cannot tell whether PayPal acted on the request.
    outcome_unknown = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        issue: str | None = None,
        debug_id: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.issue = issue
        self.debug_id = debug_id


class PayPalConfigError(PayPalError):
    """Credentials missing, or a non-Sandbox URL was configured."""


class PayPalAuthError(PayPalError):
    """PayPal rejected the credentials or access token."""


class PayPalUnavailableError(PayPalError):
    """Network error, timeout, rate limit or PayPal server error."""

    outcome_unknown = True


class PayPalMalformedResponseError(PayPalError):
    """PayPal replied with a body we cannot interpret."""

    outcome_unknown = True


class PayPalRejectedError(PayPalError):
    """PayPal explicitly refused the request (HTTP 4xx)."""


def is_valid_order_id(order_id: object) -> bool:
    return isinstance(order_id, str) and bool(_ORDER_ID_RE.match(order_id))


def _require_order_id(order_id: str) -> str:
    # Order IDs are placed in URL paths, so reject anything unexpected.
    if not is_valid_order_id(order_id):
        raise PayPalMalformedResponseError("Invalid PayPal order ID format.")
    return order_id


def _sandbox_base_url() -> str:
    base_url = os.getenv("PAYPAL_BASE_URL", config.SANDBOX_BASE_URL)
    base_url = (base_url or config.SANDBOX_BASE_URL).strip().rstrip("/")

    if base_url != config.SANDBOX_BASE_URL:
        raise PayPalConfigError("Only PayPal Sandbox is enabled.")

    return base_url


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_transport)


def _safe_code(value: object, pattern: re.Pattern) -> str | None:
    if isinstance(value, str) and pattern.match(value):
        return value
    return None


def _error_from_response(response: httpx.Response, operation: str) -> PayPalError:
    """Translate an HTTP error into a typed, credential-free exception."""
    status = response.status_code
    issue = None
    debug_id = None

    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict):
        details = body.get("details")
        if isinstance(details, list) and details and isinstance(details[0], dict):
            issue = _safe_code(details[0].get("issue"), _PAYPAL_CODE_RE)
        if issue is None:
            issue = _safe_code(body.get("name"), _PAYPAL_CODE_RE)
        debug_id = _safe_code(body.get("debug_id"), _DEBUG_ID_RE)

    logger.warning(
        "PayPal HTTP error operation=%s status=%s issue=%s debug_id=%s",
        operation,
        status,
        issue,
        debug_id,
    )

    extra = {"status_code": status, "issue": issue, "debug_id": debug_id}

    if status in (401, 403):
        return PayPalAuthError(
            "PayPal rejected the credentials. "
            "Check your Sandbox Client ID and Secret.",
            **extra,
        )

    if status == 429 or status >= 500:
        return PayPalUnavailableError(
            f"PayPal is temporarily unavailable (HTTP {status}) while {operation}.",
            **extra,
        )

    return PayPalRejectedError(
        f"PayPal rejected the request while {operation} (HTTP {status}).",
        **extra,
    )


async def _send(
    method: str,
    url: str,
    *,
    operation: str,
    timeout: float,
    **kwargs,
) -> dict:
    try:
        async with _client(timeout) as client:
            response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        logger.warning("PayPal timeout operation=%s", operation)
        raise PayPalUnavailableError(
            f"PayPal timed out while {operation}."
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("PayPal network error operation=%s", operation)
        raise PayPalUnavailableError(
            f"Could not connect to PayPal while {operation}."
        ) from exc

    if response.status_code >= 400:
        raise _error_from_response(response, operation)

    try:
        data = response.json()
    except ValueError as exc:
        raise PayPalMalformedResponseError(
            f"PayPal returned an unreadable response while {operation}."
        ) from exc

    if not isinstance(data, dict):
        raise PayPalMalformedResponseError(
            f"PayPal returned an unexpected response while {operation}."
        )

    return data


async def get_access_token() -> dict:
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
    base_url = _sandbox_base_url()

    if not client_id or not client_secret:
        raise PayPalConfigError("PayPal credentials are missing.")

    data = await _send(
        "POST",
        f"{base_url}/v1/oauth2/token",
        operation="authenticating",
        timeout=20.0,
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
    )

    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise PayPalMalformedResponseError(
            "PayPal did not return an access token."
        )

    return {
        "access_token": access_token,
        "token_type": data.get("token_type", "Bearer"),
        "expires_in": data.get("expires_in"),
    }


def _bearer(token_data: dict) -> dict:
    return {
        "Authorization": f"Bearer {token_data['access_token']}",
        "Content-Type": "application/json",
    }


def _is_paypal_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    host = parts.hostname or ""
    return parts.scheme == "https" and (
        host == "paypal.com" or host.endswith(".paypal.com")
    )


async def create_paypal_order(
    amount: str,
    description: str,
    plan_id: str,
) -> dict:
    token_data = await get_access_token()
    base_url = _sandbox_base_url()

    cancel_url = (
        f"{config.paypal_cancel_url()}?{urlencode({'plan_id': plan_id})}"
    )

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": plan_id,
                "custom_id": plan_id,
                "description": description[:127],
                "amount": {"currency_code": "USD", "value": amount},
            }
        ],
        "application_context": {
            "brand_name": "NEXTPAY",
            "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": config.paypal_return_url(),
            "cancel_url": cancel_url,
        },
    }

    # Deterministic per plan: if the request is repeated (double click, retry
    # after a dropped connection) PayPal returns the SAME order instead of
    # creating a second one.
    request_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"nextpay-create-order:{plan_id}")
    )

    data = await _send(
        "POST",
        f"{base_url}/v2/checkout/orders",
        operation="creating the order",
        timeout=30.0,
        json=payload,
        headers={**_bearer(token_data), "PayPal-Request-Id": request_id},
    )

    order_id = data.get("id")
    links = data.get("links")
    approval_url = next(
        (
            link.get("href")
            for link in (links if isinstance(links, list) else [])
            if isinstance(link, dict) and link.get("rel") == "approve"
        ),
        None,
    )

    if not is_valid_order_id(order_id) or not approval_url:
        raise PayPalMalformedResponseError(
            "PayPal did not return an order ID and approval URL."
        )

    # The browser is sent to this URL, so only accept PayPal hosts.
    if not _is_paypal_https_url(approval_url):
        raise PayPalMalformedResponseError(
            "PayPal returned an unexpected approval URL."
        )

    return {
        "paypal_order_id": order_id,
        "status": data.get("status"),
        "approval_url": approval_url,
    }


async def capture_paypal_order(order_id: str) -> dict:
    _require_order_id(order_id)
    token_data = await get_access_token()
    base_url = _sandbox_base_url()

    # Deterministic request ID: repeating this call can never charge twice.
    request_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"nxtpay-capture:{order_id}")
    )

    return await _send(
        "POST",
        f"{base_url}/v2/checkout/orders/{order_id}/capture",
        operation="capturing the payment",
        timeout=30.0,
        headers={**_bearer(token_data), "PayPal-Request-Id": request_id},
        json={},
    )


async def get_paypal_order(order_id: str) -> dict:
    _require_order_id(order_id)
    token_data = await get_access_token()
    base_url = _sandbox_base_url()

    return await _send(
        "GET",
        f"{base_url}/v2/checkout/orders/{order_id}",
        operation="checking the order status",
        timeout=30.0,
        headers=_bearer(token_data),
    )
