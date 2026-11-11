"""Guard: every protected plan request must send the plan access headers.

Background: a rewrite of the result page and the API helper once dropped
`planAccessHeaders(planId)` from plan requests, silently removing plan-level
access protection from the frontend. This test finds that class of bug.

It needs no FastAPI and does not depend on HOW tokens are generated or
stored; it only enforces the contract the project already uses:

  * api.js exports `planAccessHeaders` and `storePlanAccessToken`;
  * every `apiRequest("/api/plans/<id>...")` call passes
    `headers: planAccessHeaders(...)`;
  * the call that CREATES a plan (`POST /api/plans`, no id yet) is exempt,
    and the token it returns is stored via `storePlanAccessToken(...)`.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"


def api_request_calls(source: str):
    """Yield the full text of each `apiRequest( ... )` call (balanced parens)."""
    for match in re.finditer(r"\bapiRequest\(", source):
        start = match.end()
        depth, i = 1, start
        while i < len(source) and depth:
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            i += 1
        yield source[start : i - 1]


def first_argument(call: str) -> str:
    depth = 0
    for i, ch in enumerate(call):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            return call[:i]
    return call


def is_plan_creation(path_arg: str) -> bool:
    return bool(re.fullmatch(r"\s*[\"'`]/api/plans[\"'`]\s*", path_arg))


def unprotected_plan_calls(source: str):
    """apiRequest calls to /api/plans/<something> without planAccessHeaders."""
    missing = []
    for call in api_request_calls(source):
        path_arg = first_argument(call)
        if "/api/plans" not in path_arg or is_plan_creation(path_arg):
            continue
        if "planAccessHeaders(" not in call:
            missing.append(" ".join(call.split())[:110])
    return missing


# --- detector self-tests (synthetic sources) --------------------------------


def test_detector_flags_a_plan_call_that_forgot_the_headers():
    source = 'await apiRequest(`/api/plans/${id}`, { signal: s });'

    assert unprotected_plan_calls(source) == ["`/api/plans/${id}`, { signal: s }"]


def test_detector_accepts_calls_that_pass_the_headers():
    source = """
      await apiRequest(`/api/plans/${encodeURIComponent(planId)}`, {
        signal: current.signal,
        headers: planAccessHeaders(planId),
      });
      await apiRequest(`/api/plans/${id}/paypal/reconcile`, {
        method: "POST",
        headers: planAccessHeaders(id),
      });
    """

    assert unprotected_plan_calls(source) == []


def test_detector_exempts_plan_creation_and_unrelated_endpoints():
    source = """
      await apiRequest("/api/plans", { method: "POST", body: b });
      await apiRequest("/api/agent/recommend", { method: "POST", body: b });
    """

    assert unprotected_plan_calls(source) == []


# --- the real project ---------------------------------------------------------


def frontend_sources():
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(SRC.glob("*.js*"))
        if ".test." not in path.name
    }


def test_api_module_provides_the_plan_access_helpers():
    api = (SRC / "api.js").read_text(encoding="utf-8")

    for name in ("planAccessHeaders", "storePlanAccessToken"):
        assert re.search(
            rf"export\s+(async\s+)?(function\s+{name}\b|const\s+{name}\b)", api
        ), f"api.js does not export {name}()"


@pytest.mark.parametrize("name", ["App.jsx", "PaymentResult.jsx"])
def test_every_protected_plan_request_sends_the_plan_access_headers(name):
    missing = unprotected_plan_calls(frontend_sources()[name])

    assert not missing, (
        f"{name}: plan requests without planAccessHeaders(): {missing}"
    )


def test_the_token_returned_when_a_plan_is_created_is_stored():
    callers = {
        name: source
        for name, source in frontend_sources().items()
        if name != "api.js"
    }

    assert any("storePlanAccessToken(" in source for source in callers.values()), (
        "no code stores the access token after a plan is created"
    )


def test_header_merging_is_still_used_by_the_api_helper():
    api = (SRC / "api.js").read_text(encoding="utf-8")

    assert "buildRequestHeaders(body, callerHeaders)" in api
