"""Shared prompt scaffolding.

The schema is ~6k tokens and every Stage 1 / Stage 2 / fallback call needs
it. Sending it as a byte-identical, hour-cached `system` block means it is
written to the cache once and read back at ~10% of the price on every
later call (per model) instead of paying full input price each time.
"""

from __future__ import annotations

_SCHEMA_INTRO = (
    "Schema of the local SQLite database of NFL statistics, "
    "seasons 2021 through 2024:\n\n"
)


def cached_schema_system(schema_text: str) -> dict:
    """A cache-marked `system` block holding the schema."""
    return {
        "type": "text",
        "text": _SCHEMA_INTRO + schema_text,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }
