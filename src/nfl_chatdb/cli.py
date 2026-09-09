"""Command-line interface: ask one question, print the answer."""

from __future__ import annotations

import argparse
import json

import anthropic

from nfl_chatdb.database import DEFAULT_DB_PATH, QueryError, connect
from nfl_chatdb.formatting import format_result_sample
from nfl_chatdb.pipeline import PipelineOutcome, answer_question
from nfl_chatdb.schema import load_schema_text
from nfl_chatdb.stage1_sql import Stage1Error


def build_client():
    from dotenv import load_dotenv

    # Load ANTHROPIC_API_KEY from a local .env if present; a no-op otherwise.
    load_dotenv()
    return anthropic.Anthropic()


def render_outcome(outcome: PipelineOutcome) -> str:
    lines = [
        "SQL:",
        f"  {outcome.sql}",
        "",
        format_result_sample(outcome.result),
    ]
    if outcome.caveated:
        lines += ["", "⚠ Caveat: this answer may not fully match the question."]
        lines += [f"  - {issue}" for issue in outcome.verdict.issues]
    return "\n".join(lines)


def _outcome_to_dict(outcome: PipelineOutcome) -> dict:
    return {
        "question": outcome.question,
        "sql": outcome.sql,
        "columns": outcome.result.columns,
        "rows": [list(r) for r in outcome.result.rows],
        "row_count": outcome.result.row_count,
        "caveated": outcome.caveated,
        "issues": outcome.verdict.issues,
        "semantic_retries": outcome.semantic_retries,
        "stage1_attempts": outcome.stage1_attempts,
    }


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
        outcome = answer_question(
            build_client(), args.question, conn=conn, schema_text=schema_text
        )
    except Stage1Error as err:
        print(f"Could not produce a working query: {err.last_error}")
        return 1
    except anthropic.APIError as err:
        print(f"Anthropic API call failed: {err}")
        return 2

    if args.json:
        print(json.dumps(_outcome_to_dict(outcome), indent=2, default=str))
    else:
        print(render_outcome(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
