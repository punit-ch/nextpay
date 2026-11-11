"""A FastAPI TestClient that behaves like the real frontend.

It remembers the access token returned by ``POST /api/plans`` and sends it as
``X-Plan-Token`` on later requests for that plan, exactly as the browser does.
An explicit header always wins, so tests can still send a wrong or missing
token. Use a plain ``TestClient`` to act as a caller with no token at all.
"""

import re

from fastapi.testclient import TestClient

from app import plan_access

_PLAN_PATH = re.compile(r"^/api/plans/([^/?#]+)")


class PlanClient(TestClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan_tokens = {}

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        match = _PLAN_PATH.match(str(url))
        sends_token = any(
            name.lower() == plan_access.HEADER_NAME.lower() for name in headers
        )

        if match and not sends_token and match.group(1) in self.plan_tokens:
            headers[plan_access.HEADER_NAME] = self.plan_tokens[match.group(1)]

        response = super().request(method, url, headers=headers, **kwargs)

        if (
            method.upper() == "POST"
            and str(url).split("?")[0] == "/api/plans"
            and response.status_code == 201
        ):
            body = response.json()
            self.plan_tokens[body["plan_id"]] = body["access_token"]

        return response
