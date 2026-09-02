"""Load the static schema snapshot that Stage 1 and Stage 2 embed in prompts."""

from __future__ import annotations

from pathlib import Path

from nfl_chatdb.ingest import SCHEMA_SNAPSHOT_PATH


def load_schema_text(path: Path | None = None) -> str:
    path = Path(path) if path is not None else SCHEMA_SNAPSHOT_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"schema snapshot not found at {path}. "
            "Run `uv run python -m nfl_chatdb.ingest` to generate it."
        )
    return path.read_text()
