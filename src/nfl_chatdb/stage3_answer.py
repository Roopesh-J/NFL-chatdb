"""Stage 3: turn the settled result into a plain-English answer.

Runs once, after all retries and the fallback. Replaces the redundant
second Stage 2 call: after a retry there is no fresh verdict, so Stage 3
is the terminal read — it phrases the answer and flags when the result
does not really answer the question (empty, a degenerate one-row
"ranking", implausible numbers, an unanswerably vague question).

Stage 3 is cosmetic: if its API call fails it degrades to no sentence
plus whatever Stage 2 concluded, never an error.
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel

from nfl_chatdb.model import Usage, call_structured
from nfl_chatdb.stage2_validate import Stage2Verdict

STAGE3_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = (
    "You are given a question about NFL statistics, the SQL that answered "
    "it, and the result. Write a 1-3 sentence plain-English answer that "
    "states the key number(s). When the SQL made a debatable modelling "
    "choice - 'best' interpreted as win percentage, a minimum-games "
    "threshold, regular season only - name that choice in the answer. "
    "Set reliable=false when the result does not really answer the "
    "question: it is empty, it is a one-row 'ranking' resting on a tiny "
    "sample, the numbers are implausible, or the question is too vague to "
    "answer cleanly - and make the answer say so plainly. Otherwise "
    "reliable=true."
)


class AnswerSummary(BaseModel):
    answer: str
    reliable: bool


def _user_content(
    question: str, sql: str, result_sample: str, verdict: Stage2Verdict
) -> str:
    parts = [
        f"Question: {question}",
        "",
        "SQL:",
        sql,
        "",
        "Result:",
        result_sample,
    ]
    if not verdict.valid and verdict.issues:
        parts += [
            "",
            "A reviewer flagged this query - weigh these when judging reliability:",
            "\n".join(f"- {issue}" for issue in verdict.issues),
        ]
    return "\n".join(parts)


def synthesize_answer(
    client,
    question: str,
    sql: str,
    result_sample: str,
    verdict: Stage2Verdict,
    *,
    usage: Usage | None = None,
) -> AnswerSummary:
    try:
        summary = call_structured(
            client,
            model=STAGE3_MODEL,
            schema_text="",
            instruction=SYSTEM_PROMPT,
            user_content=_user_content(question, sql, result_sample, verdict),
            output_format=AnswerSummary,
            max_tokens=1024,
            usage=usage,
        )
    except anthropic.AnthropicError:
        summary = None

    if summary is None:
        # No sentence; fall back to Stage 2's judgment for the flag.
        return AnswerSummary(answer="", reliable=verdict.valid)
    return summary
