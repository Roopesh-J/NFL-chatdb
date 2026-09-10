"""Stage 1: generate SQL from a question and self-correct on execution errors."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nfl_chatdb.database import QueryError, QueryResult, run_query
from nfl_chatdb.model import Usage, call_text

STAGE1_MODEL = "claude-haiku-4-5"
# The semantic retry is the hard case by definition — the first attempt
# already failed review. Spend a stronger model on it.
STAGE1_RETRY_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = (
    "You translate questions about NFL statistics into a single SQLite "
    "SELECT query. Use only the tables and columns in the schema above. "
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

FALLBACK_SYSTEM = (
    "A query answered a question but returned no rows, most likely because "
    "it over-filtered (a HAVING threshold, a rare situation). Using the "
    "schema above, write one SQLite SELECT that lists the raw rows relevant "
    "to the question - the records matching the situation it describes - "
    "with NO aggregation, GROUP BY, HAVING, window functions, or ranking. "
    "Include the columns a reader needs to understand the situation. End "
    "with LIMIT 50. Return only the SQL in a ```sql block. If nothing in "
    "the schema could match, return exactly: SELECT NULL AS nothing WHERE 0"
)

_MAX_SQL_TOKENS = 2048

_FENCE_RE = re.compile(r"```(.*?)```", re.DOTALL)
# SQL always starts with SELECT or WITH — anchor on that rather than try to
# parse the optional language tag (```sql, ```sql SELECT..., ```SELECT...).
_SQL_START_RE = re.compile(r"\b(?:SELECT|WITH)\b", re.IGNORECASE)


@dataclass
class RetryContext:
    """What Stage 2 sends back when it rejects the first attempt."""

    correction: str
    previous_sql: str
    previous_sample: str


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
    body = (match.group(1) if match else text).strip()
    start = _SQL_START_RE.search(body)
    sql = body[start.start() :].strip() if start else body
    if not sql:
        raise Stage1Error(last_sql="", last_error="model returned no SQL")
    return sql


def _first_user_content(question: str, retry: RetryContext | None) -> str:
    parts = [f"Question: {question}"]
    if retry is not None:
        parts += [
            "",
            "A reviewer rejected your previous attempt at this question.",
            "",
            "Previous SQL:",
            retry.previous_sql,
        ]
        if retry.previous_sample:
            parts += ["", "Result it produced:", retry.previous_sample]
        parts += [
            "",
            "Every point the reviewer raised — address all of them:",
            retry.correction,
        ]
    return "\n".join(parts)


def _feedback(messages: list[dict], reply: str, instruction: str) -> None:
    messages.append({"role": "assistant", "content": reply})
    messages.append({"role": "user", "content": instruction})


def generate_sql(
    client,
    question: str,
    schema_text: str,
    conn,
    *,
    retry: RetryContext | None = None,
    model: str = STAGE1_MODEL,
    max_attempts: int = 2,
    usage: Usage | None = None,
) -> Stage1Result:
    messages = [{"role": "user", "content": _first_user_content(question, retry)}]

    for attempt in range(1, max_attempts + 1):
        reply, truncated = call_text(
            client,
            model=model,
            schema_text=schema_text,
            instruction=SYSTEM_PROMPT,
            messages=messages,
            max_tokens=_MAX_SQL_TOKENS,
            usage=usage,
        )
        sql = extract_sql(reply)
        last_attempt = attempt == max_attempts

        try:
            result = run_query(conn, sql)
        except QueryError as err:
            if last_attempt:
                raise Stage1Error(last_sql=sql, last_error=str(err)) from err
            hint = " (the reply was cut off)" if truncated else ""
            _feedback(
                messages,
                reply,
                f"That query failed with: {err}{hint}\nReturn a corrected SQL query.",
            )
            continue

        if result.row_count == 0 and not last_attempt:
            _feedback(
                messages,
                reply,
                "That query returned no rows. If that seems wrong, return a "
                "corrected SQL query; otherwise return the same query.",
            )
            continue

        return Stage1Result(sql, result, attempt, degenerate=result.row_count == 0)

    raise RuntimeError("unreachable")  # pragma: no cover


def generate_fallback_sql(
    client, question, failed_sql, schema_text, conn, *, usage: Usage | None = None
):
    """A ranking query returned nothing — ask for the raw matching rows.

    Returns ``(sql, QueryResult)`` or ``None`` if the query couldn't run.
    """
    content = "\n".join(
        [
            f"Question: {question}",
            "",
            "This query answered it but returned no rows:",
            failed_sql,
        ]
    )
    reply, _ = call_text(
        client,
        model=STAGE1_RETRY_MODEL,
        schema_text=schema_text,
        instruction=FALLBACK_SYSTEM,
        messages=[{"role": "user", "content": content}],
        max_tokens=1500,
        usage=usage,
    )
    sql = extract_sql(reply)
    try:
        return sql, run_query(conn, sql)
    except QueryError:
        return None
