"""Read-only access to the local NFL SQLite database."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path("data/nfl.db")

# Upper bound on rows returned by a single query. `play_by_play` has ~400
# columns, so an unbounded `SELECT *` can pull hundreds of MB into memory.
MAX_RESULT_ROWS = 10_000

# Wall-clock ceiling on a single query. `play_by_play` is ~200k wide rows
# with no indexes, so a multi-scan query Stage 1 dreams up can run for a
# minute or more; without this the desktop app just spins. On timeout the
# query is aborted and Stage 1 retries with a "too slow" hint.
DEFAULT_QUERY_TIMEOUT_SECONDS = 25.0

_ALLOWED_LEADING_KEYWORDS = {"SELECT", "WITH"}


class QueryError(Exception):
    """A query could not be run (rejected, or failed in SQLite)."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    row_count: int
    truncated: bool = False


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    if not db_path.is_file():
        raise QueryError(f"database file not found: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _strip_leading_comments(sql: str) -> str:
    """Drop only leading blank lines and full-line ``--`` comments.

    Inline ``--`` (e.g. inside a string literal) is left alone; that's the
    query author's problem, not ours.
    """
    lines = sql.splitlines()
    i = 0
    while i < len(lines) and (
        not lines[i].strip() or lines[i].lstrip().startswith("--")
    ):
        i += 1
    return "\n".join(lines[i:])


def _validate_read_only(sql: str) -> str:
    # The read-only guarantee is the `mode=ro` connection (see `connect`);
    # writes fail there regardless. This check only gives a clear early
    # error and keeps a hallucinated `DELETE` from reaching SQLite.
    cleaned = _strip_leading_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        raise QueryError("empty SQL statement")
    leading = cleaned.split(None, 1)[0].upper()
    if leading not in _ALLOWED_LEADING_KEYWORDS:
        raise QueryError(f"only SELECT / WITH statements are allowed, got: {leading}")
    return cleaned


def run_query(
    conn: sqlite3.Connection,
    sql: str,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
) -> QueryResult:
    statement = _validate_read_only(sql)

    deadline = time.monotonic() + timeout_seconds
    timed_out = False

    def _watchdog() -> int:
        nonlocal timed_out
        if time.monotonic() > deadline:
            timed_out = True
            return 1  # non-zero aborts the running statement
        return 0

    # Fires roughly every N SQLite VM steps, during execute() and fetch.
    conn.set_progress_handler(_watchdog, 2000)
    try:
        cursor = conn.execute(statement)
        fetched = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        columns = [d[0] for d in cursor.description] if cursor.description else []
    except sqlite3.Error as exc:
        if timed_out:
            raise QueryError(
                f"query exceeded the {timeout_seconds:.0f}s time limit "
                "(too complex, or scanning too much data)"
            ) from exc
        raise QueryError(str(exc)) from exc
    finally:
        conn.set_progress_handler(None, 0)
    truncated = len(fetched) > MAX_RESULT_ROWS
    if truncated:
        fetched = fetched[:MAX_RESULT_ROWS]
    rows = [tuple(row) for row in fetched]
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
    )
