from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ai_recommendation_endpoint_is_rate_limited():
    # Invalid payloads still exercise the HTTP middleware without calling OpenRouter.
    for _ in range(10):
        response = client.post("/api/agent/recommend", json={"message": "x"})
        assert response.status_code == 422

    limited = client.post("/api/agent/recommend", json={"message": "x"})
    assert limited.status_code == 429
    assert limited.headers["Retry-After"].isdigit()


def test_plan_creation_endpoint_is_rate_limited():
    # Validation failures count too, preventing cheap malformed-request floods.
    for _ in range(20):
        response = client.post("/api/plans", json={})
        assert response.status_code == 422

    limited = client.post("/api/plans", json={})
    assert limited.status_code == 429
    assert limited.headers["Retry-After"].isdigit()


def test_rate_limits_are_scoped_per_endpoint():
    for _ in range(10):
        client.post("/api/agent/recommend", json={"message": "x"})

    assert client.post("/api/agent/recommend", json={"message": "x"}).status_code == 429
    # The AI limit does not consume the separate plan-creation allowance.
    assert client.post("/api/plans", json={}).status_code == 422
