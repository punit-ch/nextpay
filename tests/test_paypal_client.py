"""PayPal client behaviour against a mocked HTTP transport."""

import asyncio
import json
import logging

import httpx
import pytest

from app import paypal_client
from app.paypal_client import (
    PayPalAuthError,
    PayPalConfigError,
    PayPalMalformedResponseError,
    PayPalRejectedError,
    PayPalUnavailableError,
)

BASE = "https://api-m.sandbox.paypal.com"


def run(coro):
    return asyncio.run(coro)


def install(monkeypatch, handler):
    """Route every request through `handler(request) -> httpx.Response`."""
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        paypal_client, "_transport", httpx.MockTransport(recording)
    )
    return calls


def token_ok(request):
    return httpx.Response(200, json={"access_token": "tok-123", "token_type": "Bearer", "expires_in": 300})


def router(**routes):
    """routes: path suffix -> response or callable. Token route is automatic."""

    def handler(request):
        path = request.url.path
        if path == "/v1/oauth2/token":
            return token_ok(request)
        for suffix, value in routes.items():
            if path.endswith(suffix):
                return value(request) if callable(value) else value
        return httpx.Response(404, json={"name": "NOT_FOUND"})

    return handler


def test_access_token_success(monkeypatch):
    install(monkeypatch, router())

    data = run(paypal_client.get_access_token())

    assert data["access_token"] == "tok-123"
    assert data["expires_in"] == 300


def test_missing_credentials_raise_config_error(monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID")
    install(monkeypatch, router())

    with pytest.raises(PayPalConfigError):
        run(paypal_client.get_access_token())


def test_non_sandbox_url_is_refused(monkeypatch):
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")
    calls = install(monkeypatch, router())

    with pytest.raises(PayPalConfigError):
        run(paypal_client.get_access_token())

    assert calls == []  # nothing was sent anywhere


def test_rejected_credentials_raise_auth_error(monkeypatch):
    install(monkeypatch, lambda r: httpx.Response(401, json={"error": "invalid_client"}))

    with pytest.raises(PayPalAuthError) as excinfo:
        run(paypal_client.get_access_token())

    assert not excinfo.value.outcome_unknown
    assert "test-client-secret" not in str(excinfo.value)


def test_token_response_without_token_is_malformed(monkeypatch):
    install(monkeypatch, lambda r: httpx.Response(200, json={"token_type": "Bearer"}))

    with pytest.raises(PayPalMalformedResponseError):
        run(paypal_client.get_access_token())


def test_create_order_sends_expected_payload_and_idempotency_key(monkeypatch):
    seen = {}

    def create(request):
        seen["body"] = json.loads(request.content)
        seen["request_id"] = request.headers["PayPal-Request-Id"]
        return httpx.Response(
            201,
            json={
                "id": "ORDER-ABC",
                "status": "CREATED",
                "links": [
                    {"rel": "self", "href": f"{BASE}/v2/checkout/orders/ORDER-ABC"},
                    {"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER-ABC"},
                ],
            },
        )

    install(monkeypatch, router(**{"/v2/checkout/orders": create}))

    result = run(paypal_client.create_paypal_order("24.99", "Lamp", "plan-1"))
    again = run(paypal_client.create_paypal_order("24.99", "Lamp", "plan-1"))
    other = run(paypal_client.create_paypal_order("24.99", "Lamp", "plan-2"))

    assert result["paypal_order_id"] == "ORDER-ABC"
    assert result["approval_url"].startswith("https://www.sandbox.paypal.com/")

    unit = seen["body"]["purchase_units"][0]
    assert unit["amount"] == {"currency_code": "USD", "value": "24.99"}
    assert unit["reference_id"] == "plan-2" == unit["custom_id"]
    ctx = seen["body"]["application_context"]
    assert ctx["return_url"] == "http://127.0.0.1:8000/api/paypal/return"
    assert ctx["cancel_url"] == "http://127.0.0.1:8000/api/paypal/cancel?plan_id=plan-2"
    assert again["paypal_order_id"] == other["paypal_order_id"]


def test_create_order_request_id_is_stable_per_plan(monkeypatch):
    ids = []

    def create(request):
        ids.append(request.headers["PayPal-Request-Id"])
        return httpx.Response(201, json={
            "id": "ORDER-ABC", "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/x"}],
        })

    install(monkeypatch, router(**{"/v2/checkout/orders": create}))

    run(paypal_client.create_paypal_order("1.00", "x", "plan-1"))
    run(paypal_client.create_paypal_order("1.00", "x", "plan-1"))
    run(paypal_client.create_paypal_order("1.00", "x", "plan-2"))

    assert ids[0] == ids[1] != ids[2]


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example.com/checkoutnow?token=ORDER-ABC",
        "http://www.sandbox.paypal.com/checkoutnow",
        "https://paypal.com.evil.example/checkout",
        "javascript:alert(1)",
    ],
)
def test_create_order_rejects_non_paypal_approval_urls(monkeypatch, href):
    def create(request):
        return httpx.Response(201, json={
            "id": "ORDER-ABC", "status": "CREATED",
            "links": [{"rel": "approve", "href": href}],
        })

    install(monkeypatch, router(**{"/v2/checkout/orders": create}))

    with pytest.raises(PayPalMalformedResponseError):
        run(paypal_client.create_paypal_order("1.00", "x", "plan-1"))


def test_create_order_without_id_or_link_is_malformed(monkeypatch):
    install(monkeypatch, router(**{"/v2/checkout/orders": httpx.Response(201, json={"status": "CREATED"})}))

    with pytest.raises(PayPalMalformedResponseError):
        run(paypal_client.create_paypal_order("1.00", "x", "plan-1"))


def test_capture_uses_deterministic_request_id(monkeypatch):
    ids = []

    def capture(request):
        ids.append(request.headers["PayPal-Request-Id"])
        return httpx.Response(201, json={"id": "ORDER-ABC", "status": "COMPLETED"})

    install(monkeypatch, router(**{"/capture": capture}))

    run(paypal_client.capture_paypal_order("ORDER-ABC"))
    run(paypal_client.capture_paypal_order("ORDER-ABC"))
    run(paypal_client.capture_paypal_order("ORDER-XYZ"))

    assert ids[0] == ids[1] != ids[2]


def test_get_order_returns_json(monkeypatch):
    install(monkeypatch, router(**{"/ORDER-ABC": httpx.Response(200, json={"id": "ORDER-ABC", "status": "APPROVED"})}))

    assert run(paypal_client.get_paypal_order("ORDER-ABC"))["status"] == "APPROVED"


@pytest.mark.parametrize("bad_id", ["", "a/b", "../x", "abc?x=1", "a b", "x" * 200, "ORDER\n1"])
def test_order_ids_are_validated_before_use_in_urls(monkeypatch, bad_id):
    calls = install(monkeypatch, router())

    for func in (paypal_client.get_paypal_order, paypal_client.capture_paypal_order):
        with pytest.raises(PayPalMalformedResponseError):
            run(func(bad_id))

    assert calls == []


def test_timeout_is_unavailable_with_unknown_outcome(monkeypatch):
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_ok(request)
        raise httpx.ReadTimeout("timed out", request=request)

    install(monkeypatch, handler)

    with pytest.raises(PayPalUnavailableError) as excinfo:
        run(paypal_client.capture_paypal_order("ORDER-ABC"))

    assert excinfo.value.outcome_unknown is True
    assert "timed out" in str(excinfo.value).lower()


def test_connection_failure_is_unavailable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    install(monkeypatch, handler)

    with pytest.raises(PayPalUnavailableError):
        run(paypal_client.get_access_token())


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_server_side_errors_are_unavailable(monkeypatch, status):
    install(monkeypatch, router(**{"/ORDER-ABC": httpx.Response(status, json={"name": "X"})}))

    with pytest.raises(PayPalUnavailableError) as excinfo:
        run(paypal_client.get_paypal_order("ORDER-ABC"))

    assert excinfo.value.outcome_unknown is True


def test_definitive_rejection_exposes_paypal_issue_code(monkeypatch):
    body = {
        "name": "UNPROCESSABLE_ENTITY",
        "details": [{"issue": "INSTRUMENT_DECLINED", "description": "payer@example.com declined"}],
        "debug_id": "abc123",
    }
    install(monkeypatch, router(**{"/capture": httpx.Response(422, json=body)}))

    with pytest.raises(PayPalRejectedError) as excinfo:
        run(paypal_client.capture_paypal_order("ORDER-ABC"))

    assert excinfo.value.issue == "INSTRUMENT_DECLINED"
    assert excinfo.value.status_code == 422
    assert not excinfo.value.outcome_unknown
    assert "payer@example.com" not in str(excinfo.value)


def test_unsafe_issue_text_from_paypal_is_discarded(monkeypatch):
    body = {"details": [{"issue": "<script>alert(1)</script>"}]}
    install(monkeypatch, router(**{"/ORDER-ABC": httpx.Response(400, json=body)}))

    with pytest.raises(PayPalRejectedError) as excinfo:
        run(paypal_client.get_paypal_order("ORDER-ABC"))

    assert excinfo.value.issue is None


@pytest.mark.parametrize("content", [b"<html>gateway</html>", b"", b"[1, 2]", b'"text"'])
def test_non_object_json_is_malformed(monkeypatch, content):
    install(monkeypatch, router(**{"/ORDER-ABC": httpx.Response(200, content=content)}))

    with pytest.raises(PayPalMalformedResponseError) as excinfo:
        run(paypal_client.get_paypal_order("ORDER-ABC"))

    assert excinfo.value.outcome_unknown is True


def test_secrets_and_tokens_never_reach_the_logs(monkeypatch, caplog):
    install(monkeypatch, router(**{"/ORDER-ABC": httpx.Response(500, json={"name": "X"})}))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(PayPalUnavailableError):
            run(paypal_client.get_paypal_order("ORDER-ABC"))

    logged = caplog.text
    for secret in ("test-client-secret", "test-client-id", "tok-123", "Bearer"):
        assert secret not in logged
