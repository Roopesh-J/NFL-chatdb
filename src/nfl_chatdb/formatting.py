"""Compact text rendering of query results for prompts and CLI output."""

from __future__ import annotations

from nfl_chatdb.database import QueryResult


def _cell(value) -> str:
    return "NULL" if value is None else str(value)


def format_result_sample(result: QueryResult, max_rows: int = 8) -> str:
    if result.row_count == 0:
        return "0 rows."

    if result.row_count <= max_rows:
        header_line = f"{result.row_count} row(s)."
        shown = result.rows
    else:
        header_line = f"{result.row_count} row(s). Showing first {max_rows}:"
        shown = result.rows[:max_rows]

    lines = [header_line, " | ".join(result.columns)]
    lines.extend(" | ".join(_cell(v) for v in row) for row in shown)
    return "\n".join(lines)


def _json_cell(value):
    """Coerce a SQLite cell to something JSON-serializable."""
    if isinstance(value, (str, int, float)) or value is None:
        return value
    return str(value)  # bytes, Decimal, datetime, ...


def outcome_to_dict(outcome) -> dict:
    """Serialize a PipelineOutcome for JSON output / the desktop app bridge."""
    return {
        "question": outcome.question,
        "sql": outcome.sql,
        "columns": outcome.result.columns,
        "rows": [[_json_cell(v) for v in r] for r in outcome.result.rows],
        "row_count": outcome.result.row_count,
        "truncated": outcome.result.truncated,
        "answer": outcome.answer,
        "reliable": outcome.reliable,
        "stage1_attempts": outcome.stage1_attempts,
        "semantic_retries": outcome.semantic_retries,
        "stage2_valid": outcome.stage2_valid,
        "stage2_issues": outcome.stage2_issues,
        "fallback_note": outcome.fallback_note,
        "elapsed_s": outcome.elapsed_s,
        "cost_usd": round(outcome.usage.cost_usd, 4),
        "model_calls": outcome.usage.calls,
    }
