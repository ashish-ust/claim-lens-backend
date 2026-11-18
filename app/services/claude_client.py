"""Thin wrapper around AsyncAnthropic: JSON-only replies, robust parsing, one retry, Pydantic validation.

Never log message contents, image data or the API key.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)

REQUEST_TIMEOUT_S = 60.0
JSON_ONLY_RULE = "Reply with a single JSON object only. No prose, no markdown, no code fences."

_client: AsyncAnthropic | None = None


class ClaudeError(Exception):
    """Any failure talking to Claude or parsing its reply. Callers turn this into UNAVAILABLE."""


def get_client() -> AsyncAnthropic:
    """Lazily create the shared async client. Raises ClaudeError if no API key is configured."""
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ClaudeError("ANTHROPIC_API_KEY is not configured")
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=REQUEST_TIMEOUT_S)
    return _client


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def image_block(base64_jpeg: str) -> dict[str, Any]:
    """An Anthropic image content block. Images are always sent as JPEG after resizing."""
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64_jpeg},
    }


def extract_json(text: str) -> Any:
    """Parse a JSON object from model output, tolerating code fences and stray prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


async def ask_json(
    model: type[T],
    system: str,
    content: list[dict[str, Any]],
    max_tokens: int = 1024,
) -> T:
    """Send one request and return a validated `model` instance.

    On invalid JSON or schema mismatch, retries once with a correction message.
    Raises ClaudeError if the request fails or both attempts are invalid.
    """
    settings = get_settings()
    client = get_client()
    messages: list[dict[str, Any]] = [{"role": "user", "content": content}]

    for _attempt in range(2):
        try:
            response = await client.messages.create(
                model=settings.claude_model,
                max_tokens=max_tokens,
                system=f"{system}\n\n{JSON_ONLY_RULE}",
                messages=messages,
            )
        except Exception as exc:  # network, timeout, auth, rate limit, etc.
            raise ClaudeError(f"Claude request failed ({type(exc).__name__})") from exc

        reply = "".join(block.text for block in response.content if block.type == "text")
        try:
            return model.model_validate(extract_json(reply))
        except ValueError:  # JSONDecodeError and pydantic ValidationError both subclass ValueError
            messages = messages + [
                {"role": "assistant", "content": reply.strip() or "{}"},
                {
                    "role": "user",
                    "content": "That reply was not valid JSON matching the required fields. "
                    + JSON_ONLY_RULE,
                },
            ]

    raise ClaudeError("Claude returned invalid JSON twice")
