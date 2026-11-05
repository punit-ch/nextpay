import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()


class PayPalError(Exception):
    pass


def _sandbox_base_url() -> str:
    base_url = os.getenv(
        "PAYPAL_BASE_URL",
        "https://api-m.sandbox.paypal.com",
    ).rstrip("/")

    if base_url != "https://api-m.sandbox.paypal.com":
        raise PayPalError("Only PayPal Sandbox is enabled in this milestone.")

    return base_url


async def get_access_token() -> dict:
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
    base_url = _sandbox_base_url()

    if not client_id or not client_secret:
        raise PayPalError("PayPal credentials are missing.")

    url = f"{base_url}/v1/oauth2/token"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
            )

        response.raise_for_status()
        data = response.json()

        access_token = data.get("access_token")
        if not access_token:
            raise PayPalError("PayPal did not return an access token.")

        return {
            "access_token": access_token,
            "token_type": data.get("token_type", "Bearer"),
            "expires_in": data.get("expires_in"),
        }

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code

        if status in (401, 403):
            raise PayPalError(
                "PayPal rejected the credentials. Check your Sandbox Client ID and Secret."
            ) from exc

        raise PayPalError(f"PayPal authentication failed with HTTP {status}.") from exc

    except httpx.RequestError as exc:
        raise PayPalError(
            "Could not connect to PayPal Sandbox. Check your internet connection."
        ) from exc


async def create_paypal_order(
    amount: str,
    description: str,
    plan_id: str,
) -> dict:
    token_data = await get_access_token()
    base_url = _sandbox_base_url()

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": plan_id,
                "custom_id": plan_id,
                "description": description[:127],
                "amount": {
                    "currency_code": "USD",
                    "value": amount,
                },
            }
        ],
        "application_context": {
            "brand_name": "NEXTPAY",
            "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": "http://127.0.0.1:8000/api/paypal/return",
            "cancel_url": "http://127.0.0.1:8000/api/paypal/cancel",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/v2/checkout/orders",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token_data['access_token']}",
                    "Content-Type": "application/json",
                    "PayPal-Request-Id": str(uuid.uuid4()),
                },
            )

        response.raise_for_status()
        data = response.json()

        approval_url = next(
            (
                link["href"]
                for link in data.get("links", [])
                if link.get("rel") == "approve"
            ),
            None,
        )

        if not data.get("id") or not approval_url:
            raise PayPalError("PayPal did not return an order ID and approval URL.")

        return {
            "paypal_order_id": data["id"],
            "status": data.get("status"),
            "approval_url": approval_url,
        }

    except httpx.HTTPStatusError as exc:
        raise PayPalError(
            f"PayPal order creation failed with HTTP {exc.response.status_code}."
        ) from exc

    except httpx.RequestError as exc:
        raise PayPalError("Could not connect to PayPal while creating the order.") from exc
    
async def capture_paypal_order(order_id: str) -> dict:
    token_data = await get_access_token()

    base_url = os.getenv(
        "PAYPAL_BASE_URL",
        "https://api-m.sandbox.paypal.com"
    ).rstrip("/")

    # Stable request ID makes retries safer.
    request_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"nxtpay-capture:{order_id}")
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/v2/checkout/orders/{order_id}/capture",
                headers={
                    "Authorization": f"Bearer {token_data['access_token']}",
                    "Content-Type": "application/json",
                    "PayPal-Request-Id": request_id,
                },
                json={},
            )

        response.raise_for_status()
        return response.json()

    except httpx.HTTPStatusError as exc:
        raise PayPalError(
            f"PayPal capture failed with HTTP {exc.response.status_code}."
        ) from exc

    except httpx.RequestError as exc:
        raise PayPalError(
            "Could not connect to PayPal while capturing payment."
        ) from exc