"""Plan-level access protection (no FastAPI needed).

Covers the token logic, the database check, the migration of older
databases, and the wiring of the protection onto the endpoints.
"""

import ast
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from app import database, plan_access
from app.models import PurchasePlan
from tests.helpers import make_plan_and_payment

ROOT = Path(__file__).resolve().parents[1]


# --- token logic -------------------------------------------------------------


def test_tokens_are_long_unique_and_url_safe():
    tokens = {plan_access.generate_token() for _ in range(200)}

    assert len(tokens) == 200
    for token in tokens:
        assert re.fullmatch(r"[A-Za-z0-9_-]{40,}", token)


def test_only_a_hash_is_stored_never_the_token():
    token = plan_access.generate_token()
    hashed = plan_access.hash_token(token)

    assert hashed != token
    assert token not in hashed
    assert re.fullmatch(r"[0-9a-f]{64}", hashed)
    assert plan_access.hash_token(token) == hashed  # deterministic


@pytest.mark.parametrize(
    "token, stored, expected",
    [
        ("good-token", "HASH_OF_GOOD", True),
        ("wrong-token", "HASH_OF_GOOD", False),
        ("good-token ", "HASH_OF_GOOD", False),   # whitespace matters
        ("GOOD-TOKEN", "HASH_OF_GOOD", False),    # case matters
        ("", "HASH_OF_GOOD", False),
        (None, "HASH_OF_GOOD", False),
        ("good-token", None, False),              # legacy plan: fails closed
        ("good-token", "", False),
        (None, None, False),
        ("", "", False),
    ],
)
def test_token_matching(token, stored, expected):
    if stored == "HASH_OF_GOOD":
        stored = plan_access.hash_token("good-token")

    assert plan_access.token_matches(token, stored) is expected


def test_comparison_is_constant_time():
    source = (ROOT / "app" / "plan_access.py").read_text(encoding="utf-8")

    assert "hmac.compare_digest" in source
    assert "== stored_hash" not in source


# --- database check ------------------------------------------------------------


def plan_with_token():
    token = plan_access.generate_token()
    plan_id, _order = make_plan_and_payment(
        access_token_hash=plan_access.hash_token(token)
    )
    return plan_id, token


def test_the_right_token_unlocks_the_plan():
    plan_id, token = plan_with_token()

    plan_access.verify_plan_access(plan_id, token)  # does not raise


@pytest.mark.parametrize("bad", [None, "", "nope", "x" * 300])
def test_missing_or_wrong_tokens_are_denied(bad):
    plan_id, _token = plan_with_token()

    with pytest.raises(plan_access.PlanAccessDenied):
        plan_access.verify_plan_access(plan_id, bad)


def test_another_plans_token_does_not_unlock_this_plan():
    plan_a, token_a = plan_with_token()
    plan_b, token_b = plan_with_token()

    with pytest.raises(plan_access.PlanAccessDenied):
        plan_access.verify_plan_access(plan_a, token_b)
    with pytest.raises(plan_access.PlanAccessDenied):
        plan_access.verify_plan_access(plan_b, token_a)


def test_unknown_plan_is_not_found():
    with pytest.raises(plan_access.PlanNotFound):
        plan_access.verify_plan_access(str(uuid4()), "anything")


@pytest.mark.parametrize("attempt", [None, "", "None", "null", "undefined", "0"])
def test_legacy_plans_without_a_stored_hash_fail_closed(attempt):
    plan_id, _order = make_plan_and_payment()  # no hash: created before the upgrade

    with pytest.raises(plan_access.PlanAccessDenied):
        plan_access.verify_plan_access(plan_id, attempt)


def test_status_codes():
    assert plan_access.PlanNotFound.status_code == 404
    assert plan_access.PlanAccessDenied.status_code == 403


# --- migration of an existing database ------------------------------------------

LEGACY_SCHEMA = """
CREATE TABLE purchase_plans (
    plan_id VARCHAR NOT NULL PRIMARY KEY, goal TEXT NOT NULL,
    product_id VARCHAR NOT NULL, product_name VARCHAR NOT NULL,
    quantity INTEGER NOT NULL, unit_price VARCHAR NOT NULL,
    total VARCHAR NOT NULL, budget_limit VARCHAR NOT NULL,
    status VARCHAR NOT NULL, payment_status VARCHAR NOT NULL,
    paypal_order_id VARCHAR UNIQUE, paypal_capture_id VARCHAR UNIQUE,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
)
"""


def legacy_database(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(LEGACY_SCHEMA)
    connection.execute(
        "INSERT INTO purchase_plans VALUES "
        "('old-plan','goal','desk_lamp','Lamp',1,'24.99','24.99','100.00',"
        "'approved_by_user','completed','ORDER-OLD','CAP-OLD','2026-01-01','2026-01-01')"
    )
    connection.commit()
    connection.close()
    return path


def test_old_database_gets_the_new_column_without_losing_data(tmp_path):
    path = legacy_database(tmp_path)
    engine = create_engine(f"sqlite:///{path.as_posix()}")

    database.ensure_columns(engine)
    database.ensure_columns(engine)  # idempotent

    connection = sqlite3.connect(path)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(purchase_plans)")]
    row = connection.execute(
        "SELECT plan_id, payment_status, paypal_capture_id, access_token_hash "
        "FROM purchase_plans"
    ).fetchone()
    connection.close()

    assert "access_token_hash" in columns
    assert row == ("old-plan", "completed", "CAP-OLD", None)  # data intact, plan fails closed


def test_ensure_columns_is_a_no_op_on_an_empty_database(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")

    database.ensure_columns(engine)  # no table yet: must not raise


def test_current_model_has_the_column():
    assert hasattr(PurchasePlan, "access_token_hash")


# --- endpoint wiring (static: FastAPI is not needed to check it) ----------------

PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("GET", "/api/catalog"),
    ("POST", "/api/plans"),                 # creates the plan and issues its token
    ("POST", "/api/agent/recommend"),
    ("GET", "/api/paypal/test-auth"),
    ("GET", "/api/paypal/return"),          # PayPal browser redirect (cannot send headers)
    ("GET", "/api/paypal/cancel"),          # PayPal browser redirect
}


def declared_routes():
    tree = ast.parse((ROOT / "app" / "main.py").read_text(encoding="utf-8"))
    routes = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "app"
                and decorator.func.attr in ("get", "post", "put", "patch", "delete")
            ):
                path = decorator.args[0].value
                guarded = any(
                    keyword.arg == "dependencies"
                    and "require_plan_access" in ast.unparse(keyword.value)
                    for keyword in decorator.keywords
                )
                routes.append((decorator.func.attr.upper(), path, guarded, node.name))
    return routes


def test_every_plan_scoped_endpoint_requires_the_plan_token():
    scoped = [r for r in declared_routes() if "{plan_id}" in r[1]]

    assert len(scoped) == 5
    unguarded = [(m, p) for m, p, guarded, _ in scoped if not guarded]
    assert unguarded == [], f"plan endpoints without require_plan_access: {unguarded}"


def test_only_the_expected_endpoints_are_public():
    public = {(m, p) for m, p, guarded, _ in declared_routes() if not guarded}

    assert public == PUBLIC_ROUTES


def test_cors_allows_the_plan_token_header():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    # Without this the browser's preflight rejects every protected request.
    assert re.search(r"allow_headers=\[[^\]]*plan_access\.HEADER_NAME", main)
    assert plan_access.HEADER_NAME == "X-Plan-Token"


def test_token_is_issued_once_and_never_serialized_again():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(main)

    serializer = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "serialize_plan"
    )
    assert "access_token" not in ast.unparse(serializer)   # includes the hash column

    assert main.count('response["access_token"]') == 1      # create_plan only
    assert "access_token_hash=plan_access.hash_token(access_token)" in main


def test_header_name_matches_the_frontend():
    store = (ROOT / "frontend" / "src" / "planAccess.js").read_text(encoding="utf-8")

    assert f'"{plan_access.HEADER_NAME}"' in store
