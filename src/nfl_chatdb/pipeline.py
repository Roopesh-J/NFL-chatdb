"""End-to-end orchestration: Stage 1 writes SQL, Stage 2 verifies it (with
one semantic retry), Stage 3 phrases the settled result.

`answer_question` never raises for a model or API failure — a hard failure
comes back as a `PipelineOutcome` with `reliable=False` and an explanatory
`answer`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import anthropic

from nfl_chatdb.database import QueryResult
from nfl_chatdb.formatting import format_result_sample
from nfl_chatdb.model import Usage
from nfl_chatdb.stage1_sql import (
    STAGE1_RETRY_MODEL,
    RetryContext,
    Stage1Error,
    Stage1Result,
    generate_fallback_sql,
    generate_sql,
)
from nfl_chatdb.stage2_validate import Stage2Verdict, validate_semantics
from nfl_chatdb.stage3_answer import synthesize_answer

log = logging.getLogger("nfl_chatdb.pipeline")

_NO_MATCH_NOTE = "No records in the database match this situation."
_EMPTY_RESULT = QueryResult(columns=[], rows=[], row_count=0)


@dataclass
class PipelineOutcome:
    question: str
    sql: str
    result: QueryResult
    answer: str
    reliable: bool
    stage1_attempts: int
    semantic_retries: int
    # Stage 2's verdict on the *final* query, or None when a retry made its
    # verdict stale (the retry wasn't re-verified — Stage 3 is the terminal
    # read in that case).
    stage2_valid: bool | None
    stage2_issues: list[str] = field(default_factory=list)
    fallback_note: str | None = None
    usage: Usage = field(default_factory=Usage)
    elapsed_s: float = 0.0


def _noop(_: str) -> None:
    pass


def _format_correction(verdict: Stage2Verdict) -> str:
    """Every issue Stage 2 raised, as a bullet list, plus its suggested fix."""
    lines = list(verdict.issues)
    if verdict.suggested_fix:
        lines.append(f"Suggested fix: {verdict.suggested_fix}")
    return "\n".join(f"- {line}" for line in lines)


def _maybe_fallback(
    client,
    question,
    s1: Stage1Result,
    verdict,
    schema_text,
    conn,
    on_progress,
    usage: Usage,
) -> tuple[str, QueryResult, str | None]:
    """If a rejected ranking query came back empty, show the raw rows."""
    if not (s1.result.row_count == 0 and verdict.valid is False):
        return s1.sql, s1.result, None

    on_progress("Looking for the underlying data")
    try:
        fb = generate_fallback_sql(
            client, question, s1.sql, schema_text, conn, usage=usage
        )
    except anthropic.AnthropicError:
        fb = None

    if fb is not None and fb[1].row_count > 0:
        sql, result = fb
        note = (
            "Couldn't produce a single answer to this — showing the "
            f"{result.row_count} record(s) that match the situation."
        )
        return sql, result, note
    return s1.sql, s1.result, _NO_MATCH_NOTE


def answer_question(
    client,
    question: str,
    *,
    conn,
    schema_text: str,
    max_semantic_retries: int = 1,
    on_progress=_noop,
) -> PipelineOutcome:
    started = time.monotonic()
    usage = Usage()

    def done(outcome: PipelineOutcome) -> PipelineOutcome:
        outcome.usage = usage
        outcome.elapsed_s = round(time.monotonic() - started, 1)
        log.info(
            "answered",
            extra={
                "question": question,
                "reliable": outcome.reliable,
                "retries": outcome.semantic_retries,
                "stage2_valid": outcome.stage2_valid,
                "fallback": outcome.fallback_note is not None,
                "calls": usage.calls,
                "cost_usd": round(usage.cost_usd, 4),
                "cache_read_tokens": usage.cache_read_tokens,
                "elapsed_s": outcome.elapsed_s,
                "sql": outcome.sql,
            },
        )
        return outcome

    def failed(reason: str, sql: str = "") -> PipelineOutcome:
        return done(
            PipelineOutcome(
                question=question,
                sql=sql,
                result=_EMPTY_RESULT,
                answer=f"I couldn't answer that: {reason}",
                reliable=False,
                stage1_attempts=0,
                semantic_retries=0,
                stage2_valid=None,
            )
        )

    on_progress("Writing SQL")
    try:
        s1 = generate_sql(client, question, schema_text, conn, usage=usage)
    except Stage1Error as err:
        return failed(err.last_error, err.last_sql)
    except anthropic.AnthropicError as err:
        return failed(f"the model call failed ({err})")

    sample = format_result_sample(s1.result)
    on_progress("Checking the answer")
    try:
        verdict = validate_semantics(
            client, question, s1.sql, schema_text, sample, usage=usage
        )
    except anthropic.AnthropicError:
        verdict = Stage2Verdict(
            valid=False,
            issues=["Stage 2 could not run — the answer is unverified."],
            retry_worthwhile=False,
        )

    retries = 0
    while (
        not verdict.valid
        and verdict.retry_worthwhile
        and retries < max_semantic_retries
    ):
        retries += 1
        on_progress(f"Refining (pass {retries + 1})")
        try:
            s1 = generate_sql(
                client,
                question,
                schema_text,
                conn,
                retry=RetryContext(
                    correction=_format_correction(verdict),
                    previous_sql=s1.sql,
                    previous_sample=sample,
                ),
                model=STAGE1_RETRY_MODEL,
                usage=usage,
            )
        except (Stage1Error, anthropic.AnthropicError):
            break  # keep the pre-retry attempt; Stage 3 judges it
        sample = format_result_sample(s1.result)

    final_sql, final_result, fallback_note = _maybe_fallback(
        client, question, s1, verdict, schema_text, conn, on_progress, usage
    )

    # After a retry, `verdict` describes the *previous* query — don't feed
    # its stale issues to Stage 3 or report them as the final Stage 2 word.
    stage3_verdict = verdict if retries == 0 else Stage2Verdict(valid=True)
    stage2_valid = verdict.valid if retries == 0 else None
    stage2_issues = list(verdict.issues) if retries == 0 else []

    if fallback_note is not None:
        answer, reliable = "", False
    else:
        on_progress("Writing the answer")
        summary = synthesize_answer(
            client,
            question,
            final_sql,
            format_result_sample(final_result),
            stage3_verdict,
            usage=usage,
        )
        answer = summary.answer
        # Stage 3 (Haiku) can only *add* a caveat, never clear Stage 2's.
        reliable = summary.reliable and (stage2_valid is not False)

    return done(
        PipelineOutcome(
            question=question,
            sql=final_sql,
            result=final_result,
            answer=answer,
            reliable=reliable,
            stage1_attempts=s1.attempts,
            semantic_retries=retries,
            stage2_valid=stage2_valid,
            stage2_issues=stage2_issues,
            fallback_note=fallback_note,
        )
    )
