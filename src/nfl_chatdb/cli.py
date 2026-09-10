"""Command-line interface: ask one question, print the answer."""

from __future__ import annotations

import argparse
import json

import anthropic

from nfl_chatdb.client import build_client
from nfl_chatdb.database import DEFAULT_DB_PATH, QueryError, connect
from nfl_chatdb.formatting import format_result_sample, outcome_to_dict
from nfl_chatdb.pipeline import PipelineOutcome, answer_question
from nfl_chatdb.schema import load_schema_text


def render_outcome(outcome: PipelineOutcome) -> str:
    lines: list[str] = []
    if outcome.answer:
        lines += [outcome.answer, ""]
    lines += ["SQL:", f"  {outcome.sql}", "", format_result_sample(outcome.result)]
    if outcome.fallback_note:
        lines += ["", outcome.fallback_note]

    if outcome.semantic_retries:
        stage2 = f"rejected the first attempt, retried {outcome.semantic_retries}x"
    elif outcome.stage2_valid is True:
        stage2 = "valid"
    elif outcome.stage2_valid is False:
        stage2 = "flagged — " + "; ".join(outcome.stage2_issues)
    else:
        stage2 = "could not run"
    u = outcome.usage
    lines += [
        "",
        "— how this was answered —",
        f"Stage 1: wrote SQL ({outcome.stage1_attempts} attempt(s))",
        f"Stage 2: {stage2}",
        f"Stage 3: {'reliable' if outcome.reliable else 'not reliable'}",
        f"{u.calls} model calls · {outcome.elapsed_s}s · "
        f"~${u.cost_usd:.4f} · {u.cache_read_tokens:,} cached tokens",
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

    outcome = answer_question(client, args.question, conn=conn, schema_text=schema_text)

    if args.json:
        print(json.dumps(outcome_to_dict(outcome), indent=2))
    else:
        print(render_outcome(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
