"""Stage 2: judge whether Stage 1's SQL actually answers the question.

A pure function of (question, sql, schema, result sample) -> verdict.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from nfl_chatdb.model import Usage, call_structured

STAGE2_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = (
    "You review a SQLite query written to answer a question about NFL "
    "statistics, using the schema above. Decide whether the query truly "
    "answers the question that "
    "was asked - not merely whether it runs. Consider: does it measure the "
    "right thing, filter to the right scope (season, team, player, play "
    "type), aggregate at the right grain, and is the result sample "
    "plausible (non-empty when a list is expected, no percentages over "
    "100, no negative counts)? If it is wrong or doubtful, set valid=false, "
    "list concrete issues, and give a specific suggested_fix instruction "
    "that Stage 1 can act on. "
    "Set retry_worthwhile=false when the problem is inherent in the "
    "question - it is underspecified or ambiguous and any reasonable SQL "
    "choice is defensible, so re-running Stage 1 would not help. Set it "
    "true when a corrected query could plausibly do better."
)


class Stage2Verdict(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)
    suggested_fix: str | None = None
    retry_worthwhile: bool = True


def _user_content(question: str, sql: str, result_sample: str) -> str:
    return "\n".join(
        [
            f"Question: {question}",
            "",
            "SQL under review:",
            sql,
            "",
            "Result sample:",
            result_sample,
        ]
    )


def validate_semantics(
    client,
    question: str,
    sql: str,
    schema_text: str,
    result_sample: str,
    *,
    usage: Usage | None = None,
) -> Stage2Verdict:
    # max_tokens headroom: claude-sonnet-5 spends part of the budget on
    # extended (adaptive) thinking before the JSON verdict — deliberate, it
    # earns the latency here — and a tight cap truncates the verdict.
    verdict = call_structured(
        client,
        model=STAGE2_MODEL,
        schema_text=schema_text,
        instruction=SYSTEM_PROMPT,
        user_content=_user_content(question, sql, result_sample),
        output_format=Stage2Verdict,
        max_tokens=4096,
        usage=usage,
    )
    if verdict is None:
        # Truncated / unparseable — a Stage 2 that can't produce a verdict
        # should caveat, not pass silently.
        return Stage2Verdict(
            valid=False,
            issues=["Stage 2 could not produce a verdict."],
            retry_worthwhile=False,
        )
    return verdict
