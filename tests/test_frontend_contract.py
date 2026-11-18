"""Backend <-> frontend contract (no FastAPI needed).

Bug 5 guard: the frontend must use exactly the status strings the backend
writes, and must not scatter its own copies of them.
"""

import re
from pathlib import Path

from app import payments

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"


def frontend_statuses():
    source = (FRONTEND / "paymentState.js").read_text(encoding="utf-8")
    block = re.search(r"PAYMENT_STATUS = Object\.freeze\(\{(.*?)\}\)", source, re.S)
    assert block, "PAYMENT_STATUS not found in paymentState.js"
    return dict(re.findall(r"(\w+):\s*\"([a-z_]+)\"", block.group(1)))


def test_frontend_status_values_equal_backend_constants():
    backend = {
        "NOT_STARTED": payments.NOT_STARTED,
        "AWAITING": payments.AWAITING,
        "CAPTURING": payments.CAPTURING,
        "COMPLETED": payments.COMPLETED,
        "CANCELLED": payments.CANCELLED,
        "REVIEW": payments.REVIEW,
        "FAILED": payments.FAILED,
    }

    assert frontend_statuses() == backend


def test_backend_cancelled_value_is_what_the_frontend_checks():
    # Bug 5: both sides must agree on the spelling of the cancelled state.
    assert payments.CANCELLED == "cancelled_by_buyer"
    assert frontend_statuses()["CANCELLED"] == "cancelled_by_buyer"


def test_every_backend_status_has_a_state_machine_entry():
    assert set(frontend_statuses().values()) == set(payments.ALLOWED_TRANSITIONS)


def test_result_page_has_no_private_copies_of_status_strings():
    """Status strings may only be spelled in paymentState.js."""
    statuses = set(frontend_statuses().values())
    for name in ("PaymentResult.jsx", "App.jsx"):
        source = (FRONTEND / name).read_text(encoding="utf-8")
        for status in statuses:
            assert f'"{status}"' not in source, f"{name} hard-codes {status!r}"


def test_receipt_is_guarded_by_the_server_verified_flag():
    source = (FRONTEND / "PaymentResult.jsx").read_text(encoding="utf-8")

    assert "canShowReceipt(plan)" in source
    assert ">COMPLETED<" not in source  # no hard-coded status text


def test_api_exposes_the_verified_flag_the_frontend_relies_on():
    main = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")

    assert main.count('"payment_verified"') >= 2  # plan + reconcile responses
    assert "payment_verified" in (FRONTEND / "paymentState.js").read_text(encoding="utf-8")


def test_api_helper_merges_caller_headers_instead_of_replacing_them():
    """Regression: `{ ...rest, headers, body }` silently dropped caller headers
    (for example a plan access token) on every request."""
    api = (FRONTEND / "api.js").read_text(encoding="utf-8")

    assert "buildRequestHeaders(body, callerHeaders)" in api
    assert "headers: callerHeaders" in api  # taken out of `rest`, then merged
    assert not re.search(r"const headers = body \?", api)  # the overwriting line


def test_header_merge_helper_keeps_caller_headers_and_is_tested():
    helper = (FRONTEND / "requestHeaders.js").read_text(encoding="utf-8")
    tests = (FRONTEND / "requestHeaders.test.js").read_text(encoding="utf-8")

    assert "callerSetContentType" in helper
    assert "X-Plan-Token" in tests
    package = (FRONTEND.parent / "package.json").read_text(encoding="utf-8")
    assert "requestHeaders.test.js" in package  # `npm test` actually runs it


def test_pending_screen_tells_the_buyer_when_automatic_checking_stops():
    page = (FRONTEND / "PaymentResult.jsx").read_text(encoding="utf-8")

    assert "Automatic checking has stopped" in page
    assert "pollingStatus(" in page
    assert "Check payment status" in page  # the manual recovery action remains
