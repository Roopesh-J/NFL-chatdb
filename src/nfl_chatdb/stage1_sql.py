"""Stage 1: generate SQL from a question and self-correct on execution errors."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nfl_chatdb.database import QueryError, QueryResult, run_query

STAGE1_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = (
    "You translate questions about NFL statistics into a single SQLite "
    "SELECT query. Use only the tables and columns in the provided schema. "
    "When a column lists its allowed values (`-- values: ...`), filter "
    "using those exact literals; do not invent your own. "
    "Return only the SQL, in a ```sql fenced block, with no explanation. "
    "The query must be a single read-only SELECT (a leading WITH is allowed). "
    "The database covers NFL seasons 2021 through 2024."
)

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\s*(.*?)\s*```", re.DOTALL)


@dataclass
class Stage1Result:
    sql: str
    result: QueryResult
    attempts: int
    degenerate: bool


class Stage1Error(Exception):
    def __init__(self, last_sql: str, last_error: str):
        super().__init__(f"Stage 1 failed after retries: {last_error}")
        self.last_sql = last_sql
        self.last_error = last_error


def extract_sql(text: str) -> str:
    match = _FENCE_RE.search(text)
    sql = (match.group(1) if match else text).strip()
    if not sql:
        raise Stage1Error(last_sql="", last_error="model returned no SQL")
    return sql


def _first_user_content(question: str, schema_text: str, correction: str | None) -> str:
    parts = [
        f"Question: {question}",
        "",
        "Schema:",
        schema_text,
    ]
    if correction:
        parts += ["", f"Important correction from a reviewer: {correction}"]
    return "\n".join(parts)


def _reply_text(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text")


def generate_sql(
    client,
    question: str,
    schema_text: str,
    conn,
    *,
    correction: str | None = None,
    max_attempts: int = 2,
) -> Stage1Result:
    messages = [
        {
            "role": "user",
            "content": _first_user_content(question, schema_text, correction),
        }
    ]

    for attempt in range(1, max_attempts + 1):
        response = client.messages.create(
            model=STAGE1_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        reply = _reply_text(response)
        sql = extract_sql(reply)
        last_attempt = attempt == max_attempts

        try:
            result = run_query(conn, sql)
        except QueryError as err:
            if last_attempt:
                raise Stage1Error(last_sql=sql, last_error=str(err)) from err
            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That query failed with: {err}\n"
                        "Return a corrected SQL query."
                    ),
                }
            )
            continue

        if result.row_count == 0:
            if last_attempt:
                return Stage1Result(sql, result, attempt, degenerate=True)
            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That query returned no rows. If that seems wrong, "
                        "return a corrected SQL query; otherwise return the "
                        "same query."
                    ),
                }
            )
            continue

        return Stage1Result(sql, result, attempt, degenerate=False)

    raise AssertionError("unreachable")  # pragma: no cover
