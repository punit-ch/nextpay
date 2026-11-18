from decimal import Decimal

from app.ai_agent import AgentIntent
from app.main import app
from fastapi.testclient import TestClient
import app.main as main_module


client = TestClient(app)


def test_agent_recommendations_use_server_catalogue(monkeypatch):
    def fake_parse_intent(message):
        return AgentIntent(
            category="audio",
            budget_limit=Decimal("70"),
            currency="USD",
            preferences=["comfort", "battery life"],
            must_avoid=[],
            requires_confirmation=True,
            clarification_needed=False,
            summary="Comfortable headphones for studying.",
        )

    monkeypatch.setattr(main_module, "parse_intent", fake_parse_intent)

    response = client.post(
        "/api/agent/recommend",
        json={
            "message": (
                "I need headphones under $70 for studying. "
                "Prioritise comfort and battery life."
            )
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "recommendations_ready"
    assert body["intent"]["requires_confirmation"] is True
    assert len(body["recommendations"]) == 1
    assert body["recommendations"][0]["product_id"] == "headphones"
    assert body["recommendations"][0]["price"] == "59.00"
    assert body["payment_status"] == "not_started"


def test_agent_asks_for_missing_budget(monkeypatch):
    def fake_parse_intent(message):
        return AgentIntent(
            category="audio",
            budget_limit=None,
            currency="USD",
            preferences=["comfort"],
            must_avoid=[],
            requires_confirmation=True,
            clarification_needed=True,
            clarification_question="What is your maximum budget?",
            summary="Looking for comfortable headphones.",
        )

    monkeypatch.setattr(main_module, "parse_intent", fake_parse_intent)

    response = client.post(
        "/api/agent/recommend",
        json={"message": "I need comfortable headphones for studying."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "needs_clarification"
    assert response.json()["recommendations"] == []


def test_ai_unavailable_does_not_create_fake_recommendations(monkeypatch):
    from app.ai_agent import AIUnavailableError

    def fail_parse_intent(message):
        raise AIUnavailableError("AI provider unavailable.")

    monkeypatch.setattr(main_module, "parse_intent", fail_parse_intent)

    response = client.post(
        "/api/agent/recommend",
        json={"message": "I need headphones under $70 for studying."},
    )

    assert response.status_code == 503