"""End-to-end orchestration: Stage 1 writes SQL, Stage 2 verifies it (with
one semantic retry), Stage 3 phrases the settled result."""

from __future__ import annotations

from dataclasses import dataclass

from nfl_chatdb.database import QueryError, QueryResult, run_query
from nfl_chatdb.formatting import format_result_sample
from nfl_chatdb.prompts import cached_schema_system
from nfl_chatdb.stage1_sql import (
    STAGE1_RETRY_MODEL,
    _reply_text,
    extract_sql,
    generate_sql,
)
from nfl_chatdb.stage2_validate import Stage2Verdict, validate_semantics
from nfl_chatdb.stage3_answer import AnswerSummary, synthesize_answer

_NO_MATCH_NOTE = "No records in the database match this situation."

_FALLBACK_SYSTEM = (
    "A query answered a question but returned no rows, most likely because "
    "it over-filtered (a HAVING threshold, a rare situation). Using the "
    "schema above, write one "
    "SQLite SELECT that lists the raw rows relevant to the question - the "
    "records matching the situation it describes - with NO aggregation, "
    "GROUP BY, HAVING, window functions, or ranking. Include the columns a "
    "reader needs to understand the situation. End with LIMIT 50. Return "
    "only the SQL in a ```sql block. If nothing in the schema could match, "
    "return exactly: SELECT NULL AS nothing WHERE 0"
)


@dataclass
class PipelineOutcome:
    question: str
    sql: str
    result: QueryResult
    verdict: Stage2Verdict
    caveated: bool
    semantic_retries: int
    stage1_attempts: int
    answer: str = ""
    reliable: bool = True
    fallback_note: str | None = None


def _noop(_message: str) -> None:
    pass


def _format_correction(verdict: Stage2Verdict) -> str:
    """Every issue Stage 2 raised, as a bullet list, plus its suggested fix."""
    lines = list(verdict.issues)
    if verdict.suggested_fix:
        lines.append(f"Suggested fix: {verdict.suggested_fix}")
    return "\n".join(f"- {line}" for line in lines)


def _fallback_rows(client, question, failed_sql, schema_text, conn):
    """When a ranking query comes back empty, ask for the raw matching rows.

    Returns (sql, QueryResult) or None if the query could not be run.
    """
    content = "\n".join(
        [
            f"Question: {question}",
            "",
            "This query answered it but returned no rows:",
            failed_sql,
        ]
    )
    response = client.messages.create(
        model=STAGE1_RETRY_MODEL,
        max_tokens=1500,
        system=[
            cached_schema_system(schema_text),
            {"type": "text", "text": _FALLBACK_SYSTEM},
        ],
        messages=[{"role": "user", "content": content}],
    )
    sql = extract_sql(_reply_text(response))
    try:
        return sql, run_query(conn, sql)
    except QueryError:
        return None


def answer_question(
    client,
    question: str,
    *,
    conn,
    schema_text: str,
    max_semantic_retries: int = 1,
    on_progress=_noop,
) -> PipelineOutcome:
    on_progress("Writing SQL")
    s1 = generate_sql(client, question, schema_text, conn)
    sample = format_result_sample(s1.result)
    on_progress("Checking the answer")
    verdict = validate_semantics(client, question, s1.sql, schema_text, sample)

    retries = 0
    while (
        not verdict.valid
        and verdict.retry_worthwhile
        and retries < max_semantic_retries
    ):
        retries += 1
        on_progress(f"Refining (pass {retries + 1})")
        # One retry only, and Stage 3 is the terminal read — no second
        # Stage 2 here (a re-check verdict couldn't drive anything anyway).
        s1 = generate_sql(
            client,
            question,
            schema_text,
            conn,
            correction=_format_correction(verdict),
            previous_sql=s1.sql,
            previous_sample=sample,
            model=STAGE1_RETRY_MODEL,
        )
        sample = format_result_sample(s1.result)

    final_sql = s1.sql
    final_result = s1.result
    fallback_note = None

    # A ranking query that Stage 2 rejected *and* came back empty has almost
    # certainly over-filtered. Show the raw matching rows instead of nothing.
    if final_result.row_count == 0 and not verdict.valid:
        on_progress("Looking for the underlying data")
        fb = _fallback_rows(client, question, s1.sql, schema_text, conn)
        if fb is not None and fb[1].row_count > 0:
            final_sql, final_result = fb
            fallback_note = (
                "Couldn't produce a single answer to this — showing the "
                f"{final_result.row_count} record(s) that match the situation."
            )
        else:
            fallback_note = _NO_MATCH_NOTE

    if fallback_note is not None:
        summary = AnswerSummary(answer="", reliable=False)
    else:
        on_progress("Writing the answer")
        summary = synthesize_answer(
            client,
            question,
            final_sql,
            format_result_sample(final_result),
            verdict,
        )

    return PipelineOutcome(
        question=question,
        sql=final_sql,
        result=final_result,
        verdict=verdict,
        caveated=not summary.reliable,
        semantic_retries=retries,
        stage1_attempts=s1.attempts,
        answer=summary.answer,
        reliable=summary.reliable,
        fallback_note=fallback_note,
    )
