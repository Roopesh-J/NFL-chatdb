"""Stage 1: generate SQL from a question and self-correct on execution errors."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nfl_chatdb.database import QueryError, QueryResult, run_query

STAGE1_MODEL = "claude-haiku-4-5"
# The semantic retry is the hard case by definition — the first attempt
# already failed review. Spend a stronger model on it.
STAGE1_RETRY_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = (
    "You translate questions about NFL statistics into a single SQLite "
    "SELECT query. Use only the tables and columns in the provided schema. "
    "When a column lists its allowed values (`-- values: ...`), filter "
    "using those exact literals; do not invent your own. "
    "Pick the coarsest table that already holds what the question needs: "
    "`schedules` is one row per game (scores, spreads, results, weather); "
    "`seasonal_stats` is one row per player per season; `weekly` is one row "
    "per player per game; `rosters` is one row per player per season "
    "(position, height, weight, college, age, draft); `snap_counts` is snap "
    "participation per player per game; `play_by_play` is one row per play "
    "and is far slower - use it only for play-level detail the other tables "
    "lack. `schedules.game_id` and player GSIS `player_id` join cleanly "
    "across play_by_play / weekly / seasonal_stats / rosters. "
    "Return only the SQL, in a ```sql fenced block, with no explanation. "
    "The query must be a single read-only SELECT (a leading WITH is allowed). "
    "The database covers NFL seasons 2021 through 2024."
)

# A language tag (```sql) is only consumed when it's on its own line —
# otherwise ` ```SELECT * FROM t``` ` (fence and SQL on one line) would
# have `SELECT` eaten as if it were the tag.
_FENCE_RE = re.compile(
    r"```(?:[a-zA-Z][a-zA-Z0-9_+-]*[ \t]*\n)?\s*(.*?)\s*```", re.DOTALL
)


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


def _first_user_content(
    question: str,
    schema_text: str,
    correction: str | None,
    previous_sql: str | None,
    previous_sample: str | None,
) -> str:
    parts = [
        f"Question: {question}",
        "",
        "Schema:",
        schema_text,
    ]
    if previous_sql:
        parts += [
            "",
            "A reviewer rejected your previous attempt at this question.",
            "",
            "Previous SQL:",
            previous_sql,
        ]
        if previous_sample:
            parts += ["", "Result it produced:", previous_sample]
    if correction:
        parts += [
            "",
            "Every point the reviewer raised — address all of them:",
            correction,
        ]
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
    previous_sql: str | None = None,
    previous_sample: str | None = None,
    model: str = STAGE1_MODEL,
    max_attempts: int = 2,
) -> Stage1Result:
    messages = [
        {
            "role": "user",
            "content": _first_user_content(
                question, schema_text, correction, previous_sql, previous_sample
            ),
        }
    ]

    for attempt in range(1, max_attempts + 1):
        response = client.messages.create(
            model=model,
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
