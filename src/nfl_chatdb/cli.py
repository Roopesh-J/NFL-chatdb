"""Command-line interface: ask one question, print the answer."""

from __future__ import annotations

import argparse
import json

import anthropic

from nfl_chatdb.database import DEFAULT_DB_PATH, QueryError, connect
from nfl_chatdb.formatting import format_result_sample, outcome_to_dict
from nfl_chatdb.pipeline import PipelineOutcome, answer_question
from nfl_chatdb.schema import load_schema_text
from nfl_chatdb.stage1_sql import Stage1Error


def build_client():
    from dotenv import load_dotenv

    # Load ANTHROPIC_API_KEY from a local .env if present; a no-op otherwise.
    load_dotenv()
    return anthropic.Anthropic()


def render_outcome(outcome: PipelineOutcome) -> str:
    lines: list[str] = []
    if outcome.answer:
        lines += [outcome.answer, ""]
    lines += ["SQL:", f"  {outcome.sql}", "", format_result_sample(outcome.result)]
    if outcome.fallback_note:
        lines += ["", outcome.fallback_note]

    retry = (
        f", then {outcome.semantic_retries} semantic retry"
        if outcome.semantic_retries
        else ""
    )
    lines += [
        "",
        "— how this was answered —",
        f"Stage 1: wrote SQL{retry}",
        "Stage 2: "
        + (
            "valid"
            if outcome.verdict.valid
            else "flagged — " + "; ".join(outcome.verdict.issues)
        ),
        f"Stage 3: {'reliable' if outcome.reliable else 'not reliable'}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Ask a natural-language question about NFL statistics."
    )
    parser.add_argument("question", help="The question to answer.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of a report."
    )
    args = parser.parse_args(argv)

    try:
        schema_text = load_schema_text()
        conn = connect(args.db)
    except (QueryError, FileNotFoundError) as err:
        print(f"{err}\nRun `uv run python -m nfl_chatdb.ingest` first.")
        return 2

    try:
        client = build_client()
    except anthropic.AnthropicError as err:
        print(
            f"Could not create the Anthropic client: {err}\n"
            "Set ANTHROPIC_API_KEY (for example in a .env file)."
        )
        return 2

    try:
        outcome = answer_question(
            client, args.question, conn=conn, schema_text=schema_text
        )
    except Stage1Error as err:
        print(f"Could not produce a working query: {err.last_error}")
        return 1
    except anthropic.APIError as err:
        print(f"Anthropic API call failed: {err}")
        return 2

    if args.json:
        print(json.dumps(outcome_to_dict(outcome), indent=2, default=str))
    else:
        print(render_outcome(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
