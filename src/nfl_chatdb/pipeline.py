"""End-to-end orchestration of Stage 1 and Stage 2 with one semantic retry."""

from __future__ import annotations

from dataclasses import dataclass

from nfl_chatdb.database import QueryResult
from nfl_chatdb.formatting import format_result_sample
from nfl_chatdb.stage1_sql import generate_sql
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


def answer_question(
    client,
    question: str,
    *,
    conn,
    schema_text: str,
    max_semantic_retries: int = 1,
) -> PipelineOutcome:
    s1 = generate_sql(client, question, schema_text, conn)
    sample = format_result_sample(s1.result)
    verdict = validate_semantics(client, question, s1.sql, schema_text, sample)

    retries = 0
    while not verdict.valid and retries < max_semantic_retries:
        retries += 1
        correction = verdict.suggested_fix or "; ".join(verdict.issues)
        s1 = generate_sql(
            client, question, schema_text, conn, correction=correction
        )
        sample = format_result_sample(s1.result)
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
