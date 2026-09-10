"""End-to-end orchestration of Stage 1 and Stage 2 with one semantic retry."""

from __future__ import annotations

from dataclasses import dataclass

from nfl_chatdb.database import QueryResult
from nfl_chatdb.formatting import format_result_sample
from nfl_chatdb.stage1_sql import STAGE1_RETRY_MODEL, generate_sql
from nfl_chatdb.stage2_validate import Stage2Verdict, validate_semantics


@dataclass
class PipelineOutcome:
    question: str
    sql: str
    result: QueryResult
    verdict: Stage2Verdict
    caveated: bool
    semantic_retries: int
    stage1_attempts: int


def _noop(_message: str) -> None:
    pass


def _format_correction(verdict: Stage2Verdict) -> str:
    """Every issue Stage 2 raised, as a bullet list, plus its suggested fix."""
    lines = list(verdict.issues)
    if verdict.suggested_fix:
        lines.append(f"Suggested fix: {verdict.suggested_fix}")
    return "\n".join(f"- {line}" for line in lines)


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
    while not verdict.valid and retries < max_semantic_retries:
        retries += 1
        on_progress(f"Refining (pass {retries + 1})")
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
        on_progress("Re-checking the answer")
        verdict = validate_semantics(
            client, question, s1.sql, schema_text, sample
        )

    return PipelineOutcome(
        question=question,
        sql=s1.sql,
        result=s1.result,
        verdict=verdict,
        caveated=not verdict.valid,
        semantic_retries=retries,
        stage1_attempts=s1.attempts,
    )
