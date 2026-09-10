"""Anthropic client construction — one place, so nothing imports the CLI
just to get a client."""

from __future__ import annotations

import anthropic
from dotenv import load_dotenv

_loaded = False


def build_client() -> anthropic.Anthropic:
    """Load `.env` once, then return a client.

    Raises `anthropic.AnthropicError` if no credentials can be resolved.
    """
    global _loaded
    if not _loaded:
        load_dotenv()  # ANTHROPIC_API_KEY from a local .env, if present
        _loaded = True
    return anthropic.Anthropic()
