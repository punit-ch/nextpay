import os

import httpx
from dotenv import load_dotenv

load_dotenv()


class PayPalError(Exception):
    pass


async def get_access_token() -> dict:
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
    base_url = os.getenv(
        "PAYPAL_BASE_URL",
        "https://api-m.sandbox.paypal.com"
    ).rstrip("/")

    if not client_id or not client_secret:
        raise PayPalError("PayPal credentials are missing.")

    if base_url != "https://api-m.sandbox.paypal.com":
        raise PayPalError("Only PayPal Sandbox is enabled in this milestone.")

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

        raise PayPalError(
            f"PayPal authentication failed with HTTP {status}."
        ) from exc

    except httpx.RequestError as exc:
        raise PayPalError(
            "Could not connect to PayPal Sandbox. Check your internet connection."
        ) from exc