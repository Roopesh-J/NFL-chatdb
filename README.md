# NFL Chat-With-Your-Database

Two-stage natural-language-to-SQL pipeline over NFL statistics. Stage 1
(Claude Haiku 4.5) writes SQL and self-corrects on execution errors.
Stage 2 (Claude Sonnet 5) checks that the SQL actually answers the
question. See `docs/superpowers/specs/2026-09-01-nfl-chat-db-design.md`.

## Setup

```bash
uv sync
cp .env.example .env   # then edit .env
```

## Ingest data (one-time, ~minutes, pulls from nflverse)

```bash
uv run python -m nfl_chatdb.ingest
```

## Ask a question

```bash
uv run nfl-chatdb "How many rushing touchdowns did Derrick Henry score in 2023?"
```

## Test

```bash
uv run pytest            # unit tests (no network)
uv run pytest -m live    # live API + ingestion tests (costs money, needs data/nfl.db)
```
