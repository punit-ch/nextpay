import json
import os
from decimal import Decimal
from typing import Literal
import logging

logger = logging.getLogger("nextpay.ai")
import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

load_dotenv()


class AgentIntent(BaseModel):
    category: Literal["audio", "workspace", "other", "any"]
    budget_limit: Decimal | None = Field(default=None, gt=0)
    currency: Literal["USD"] = "USD"
    preferences: list[str] = Field(default_factory=list, max_length=5)
    must_avoid: list[str] = Field(default_factory=list, max_length=5)
    requires_confirmation: bool = True
    clarification_needed: bool = False
    clarification_question: str | None = None
    summary: str = Field(min_length=5, max_length=300)


class AIUnavailableError(Exception):
    pass


def parse_intent(user_message: str) -> AgentIntent:
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_MODEL", "openrouter/free")

    if not api_key:
        raise AIUnavailableError("OpenRouter API key is not configured.")

    system_prompt = """
You are the intent extraction component of NEXTPAY,
an AI-assisted commerce agent.

Extract the user's shopping intent into a JSON object.

Rules:
- Never invent a budget if the user did not provide one.
- Only use USD when the user explicitly uses USD or dollars.
- If another currency is used, set clarification_needed to true.
- Use category audio for headphones, speakers and audio equipment.
- Use category workspace for desk lamps, keyboards and desk equipment.
- Use category other when the category is unclear or unsupported.
- Use category any only when the user has no category preference.
- Extract preferences and things to avoid.
- requires_confirmation must always be true.
- Never claim that a purchase has been made.
- If essential information is missing, ask a clarification question.
- Return valid JSON only.

Required JSON keys:
category, budget_limit, currency, preferences, must_avoid,
requires_confirmation, clarification_needed,
clarification_question, summary

budget_limit must be a number or null.
""".strip()

    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "X-Title": "NEXTPAY",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 3000,
            },
            timeout=30,
        )
        response.raise_for_status()

        result = response.json()
        choices = result.get("choices") or []

        if not choices:
            raise AIUnavailableError(
                "AI returned no choices. "
                f"Response: {result.get('error', 'Unknown provider response')}"
            )

        choice = choices[0]
        logger.info(
        "OpenRouter response: model=%s finish_reason=%s usage=%s",
         result.get("model"),
         choice.get("finish_reason"),
         result.get("usage"),
          )
        message = choice.get("message") or {}
        content = message.get("content")

        if not content:
            finish_reason = choice.get("finish_reason", "unknown")
            refusal = message.get("refusal")

            if refusal:
                raise AIUnavailableError(
                    f"AI provider refused the request: {refusal}"
                )

            if finish_reason == "length":
                raise AIUnavailableError(
                    "AI output limit reached. Increase the token budget or use a different model."
                )

            if finish_reason == "error":
                provider_error = choice.get("error", {})
                raise AIUnavailableError(
                    f"AI provider error: {provider_error.get('message', 'Unknown error')}"
                )

            raise AIUnavailableError(
                "AI returned empty content. "
                f"Finish reason: {finish_reason}. Try again or select another model."
            )

        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AIUnavailableError(
                "AI returned invalid JSON. Please retry."
            ) from exc

        intent = AgentIntent.model_validate(data)

        # Never allow the model to remove the approval requirement.
        intent.requires_confirmation = True

        if intent.budget_limit is not None and intent.budget_limit > Decimal("1000.00"):
            raise AIUnavailableError("The requested budget exceeds the demo limit.")

        return intent

    except (httpx.HTTPError, KeyError, IndexError, ValueError, ValidationError) as exc:
        raise AIUnavailableError(
            "The AI provider returned an unavailable or invalid response."
        ) from exc
