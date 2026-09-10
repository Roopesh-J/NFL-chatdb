"""Shared plumbing for the model calls each stage makes.

Every stage sends the same shape of request: a two-block ``system`` (the
hour-cached schema, then a stage-specific instruction) plus a user
message. These helpers own that shape so the stages don't each re-spell
it, and so failure handling and usage accounting are uniform.
"""

from __future__ import annotations

from dataclasses import dataclass

import pydantic
from pydantic import BaseModel

from nfl_chatdb.prompts import cached_schema_system

# claude-sonnet-5 $2/$10, claude-haiku-4-5 $1/$5 per 1M tokens. Cache
# reads are 0.1x input; 1-hour cache writes (the ttl we use) are 2x input.
_PRICE = {
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_CACHE_WRITE_MULT = 2.0
_CACHE_READ_MULT = 0.1


@dataclass
class Usage:
    """Running total across every model call in one `answer_question`."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, model: str, usage) -> None:
        in_price, out_price = _PRICE.get(model, (0.0, 0.0))
        fresh = usage.input_tokens
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        out = usage.output_tokens
        self.calls += 1
        self.input_tokens += fresh
        self.output_tokens += out
        self.cache_write_tokens += write
        self.cache_read_tokens += read
        self.cost_usd += (
            (fresh + write * _CACHE_WRITE_MULT + read * _CACHE_READ_MULT) * in_price
            + out * out_price
        ) / 1_000_000


def _system(schema_text: str, instruction: str) -> list[dict]:
    blocks: list[dict] = []
    if schema_text:
        blocks.append(cached_schema_system(schema_text))
    blocks.append({"type": "text", "text": instruction})
    return blocks


def call_text(
    client,
    *,
    model: str,
    schema_text: str,
    instruction: str,
    messages: list[dict],
    max_tokens: int,
    usage: Usage | None = None,
) -> tuple[str, bool]:
    """One ``messages.create``. Returns ``(reply_text, was_truncated)``.

    Raises ``anthropic.AnthropicError`` on an API failure — the caller
    decides how to degrade.
    """
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_system(schema_text, instruction),
        messages=messages,
    )
    if usage is not None and getattr(response, "usage", None) is not None:
        usage.add(model, response.usage)
    text = "".join(b.text for b in response.content if b.type == "text")
    return text, response.stop_reason == "max_tokens"


def call_structured(
    client,
    *,
    model: str,
    schema_text: str,
    instruction: str,
    user_content: str,
    output_format: type[BaseModel],
    max_tokens: int,
    usage: Usage | None = None,
):
    """One ``messages.parse``. Returns the validated model, or ``None`` if
    the response couldn't be parsed (truncated JSON, refusal).

    Raises ``anthropic.AnthropicError`` on an API failure.
    """
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=_system(schema_text, instruction),
            messages=[{"role": "user", "content": user_content}],
            output_format=output_format,
        )
    except pydantic.ValidationError:
        return None
    if usage is not None and getattr(response, "usage", None) is not None:
        usage.add(model, response.usage)
    return response.parsed_output
