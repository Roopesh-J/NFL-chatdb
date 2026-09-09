"""Stage 2: judge whether Stage 1's SQL actually answers the question.

A pure function of (question, sql, schema, result sample) -> verdict.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

STAGE2_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = (
    "You review a SQLite query written to answer a question about NFL "
    "statistics. Decide whether the query truly answers the question that "
    "was asked - not merely whether it runs. Consider: does it measure the "
    "right thing, filter to the right scope (season, team, player, play "
    "type), aggregate at the right grain, and is the result sample "
    "plausible (non-empty when a list is expected, no percentages over "
    "100, no negative counts)? If it is wrong or doubtful, set valid=false, "
    "list concrete issues, and give a specific suggested_fix instruction "
    "that Stage 1 can act on."
)


class Stage2Verdict(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)
    suggested_fix: str | None = None


def _user_content(
    question: str, sql: str, schema_text: str, result_sample: str
) -> str:
    return "\n".join(
        [
            f"Question: {question}",
            "",
            "SQL under review:",
            sql,
            "",
            "Result sample:",
            result_sample,
            "",
            "Schema:",
            schema_text,
        ]
    )


def validate_semantics(
    client,
    question: str,
    sql: str,
    schema_text: str,
    result_sample: str,
) -> Stage2Verdict:
    response = client.messages.parse(
        model=STAGE2_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": _user_content(
                    question, sql, schema_text, result_sample
                ),
            }
        ],
        output_format=Stage2Verdict,
    )
    verdict = response.parsed_output
    if verdict is None:
        return Stage2Verdict(
            valid=False, issues=["Stage 2 returned no parseable verdict."]
        )
    return verdict
