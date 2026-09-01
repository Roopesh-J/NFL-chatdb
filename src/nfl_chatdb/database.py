"""Read-only access to the local NFL SQLite database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path("data/nfl.db")

_ALLOWED_LEADING_KEYWORDS = {"SELECT", "WITH"}


class QueryError(Exception):
    """A query could not be run (rejected, or failed in SQLite)."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    row_count: int


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    if not db_path.exists():
        raise QueryError(f"database file not found: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _strip_sql_comments(sql: str) -> str:
    out: list[str] = []
    for line in sql.splitlines():
        stripped = line.split("--", 1)[0]
        out.append(stripped)
    return "\n".join(out)


def _validate_read_only(sql: str) -> str:
    cleaned = _strip_sql_comments(sql).strip()
    if not cleaned:
        raise QueryError("empty SQL statement")
    # Reject multiple statements (allow a single trailing semicolon).
    without_trailing = cleaned.rstrip(";").strip()
    if ";" in without_trailing:
        raise QueryError("only a single SQL statement is allowed")
    leading = without_trailing.split(None, 1)[0].upper()
    if leading not in _ALLOWED_LEADING_KEYWORDS:
        raise QueryError(
            f"only SELECT / WITH statements are allowed, got: {leading}"
        )
    return without_trailing


def run_query(conn: sqlite3.Connection, sql: str) -> QueryResult:
    statement = _validate_read_only(sql)
    try:
        cursor = conn.execute(statement)
        rows = [tuple(row) for row in cursor.fetchall()]
        columns = (
            [d[0] for d in cursor.description] if cursor.description else []
        )
    except sqlite3.Error as exc:
        raise QueryError(str(exc)) from exc
    return QueryResult(columns=columns, rows=rows, row_count=len(rows))
