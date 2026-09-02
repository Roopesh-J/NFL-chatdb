# NFL Chat-With-Your-Database MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-turn natural-language-to-SQL pipeline over NFL statistics that separates "is the SQL valid" (Stage 1) from "does the SQL mean what was asked" (Stage 2), and annotates rather than blocks when a semantic mismatch survives one retry.

**Architecture:** A local read-only SQLite database is populated from three nflverse datasets via a one-time ingestion script. Queries flow through a two-stage pipeline built on the raw Anthropic SDK: Stage 1 (Claude Haiku 4.5) generates SQL and self-corrects on execution errors in a hand-rolled loop capped at 2 attempts; Stage 2 (Claude Sonnet 5) is a pure function that validates the successful query against the original question with forced structured output. A pipeline orchestrator wires the two together with a single semantic-mismatch retry, then returns the answer (caveated if still mismatched). A thin `argparse` CLI is the only interface.

**Tech Stack:** Python 3.11, `uv` for env/dependency management, `anthropic` SDK, `nfl_data_py` for ingestion, `pandas` (transitive via `nfl_data_py`), standard-library `sqlite3`, `pydantic` for Stage 2's structured output, `pytest` for tests.

**Spec:** `docs/superpowers/specs/2026-09-01-nfl-chat-db-design.md` — read it alongside this plan; the plan argues from it.

## Global Constraints

- **Python version:** 3.11 (`.python-version` pins `3.11`). Rationale: the spec-mandated `nfl_data_py` (0.3.3, latest) hard-caps `pandas<2.0` / `numpy<2.0`, and `pandas 1.5.3` (newest `<2.0`) has no cp312 wheels and fails to build on 3.12. 3.11 has prebuilt wheels for the whole chain. `uv` auto-downloads a managed CPython 3.11.
- **Dependency manager:** `uv` only. Every run command is `uv run ...`; every dependency add is `uv add ...`. Do not create a bare `venv` or use `pip` directly.
- **Stage 1 model:** exact string `claude-haiku-4-5`. Never append a date suffix.
- **Stage 2 model:** exact string `claude-sonnet-5`. Never append a date suffix.
- **Database is read-only at query time:** the query connection is opened with `mode=ro`; `run_query` additionally rejects any statement that is not a single `SELECT`/`WITH`.
- **No semantic/glossary layer on the database** — this is fixed by design (see spec Overview). Do not add table/column documentation beyond the raw schema snapshot.
- **Single-turn only:** no conversation history is carried between questions.
- **Retry caps:** Stage 1 execution-retry loop = 2 attempts total (initial + 1). Stage 2 → Stage 1 semantic retry = 1.
- **Seasons ingested for MVP:** `[2021, 2022, 2023, 2024, 2025]` (the last 5 completed NFL seasons as of 2026-09-01). The ingestion CLI accepts `--seasons` to override for development.
- **Package name:** `nfl_chatdb`, importable as `import nfl_chatdb`. Source lives under `src/nfl_chatdb/`.
- **API key:** read from `ANTHROPIC_API_KEY` in the environment (or an `ant auth login` profile). Never hardcode. `.env` is git-ignored; `.env.example` is committed.
- **Anthropic SDK note:** `anthropic` 1.x is built on `httpx2`. Use `client.messages.create(...)` for Stage 1 and `client.messages.parse(..., output_format=Model)` for Stage 2. Do not use assistant-message prefills (rejected on both models).
- **Commit after every task** (frequent commits). Each task's final step is a commit. The `git commit -m "..."` lines shown in each task are the *subject only* — every commit message must also end with these two trailer lines (blank line before them):

  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Meh8doDoJB9WWsFfPLeNN9
  ```

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `src/nfl_chatdb/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `nfl_chatdb.__version__: str` — the package version string `"0.1.0"`.
  - `uv run pytest` runs the test suite.
  - `uv run nfl-chatdb` resolves to `nfl_chatdb.cli:main` (the module is created in Task 9; the entry point is declared now and will fail to import until then — that is expected).
  - pytest marker `live` registered — tests marked `@pytest.mark.live` are deselected by default via `addopts = "-m 'not live'"`.

- [ ] **Step 1: Install uv**

`uv` is not present on this machine. Install it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then restart the shell or `source $HOME/.local/bin/env` so `uv` is on `PATH`. Verify:

```bash
uv --version
```

Expected: prints a version (e.g. `uv 0.4.x` or newer).

- [ ] **Step 2: Create `.python-version`**

```
3.11
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "nfl-chatdb"
version = "0.1.0"
description = "Two-stage natural-language-to-SQL pipeline over NFL statistics"
readme = "README.md"
requires-python = ">=3.11,<3.12"
dependencies = [
    "anthropic>=1.0",
    "nfl-data-py>=0.3.2",
    "pydantic>=2.0",
]

[project.scripts]
nfl-chatdb = "nfl_chatdb.cli:main"

[dependency-groups]
dev = [
    "pytest>=8.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/nfl_chatdb"]

[tool.pytest.ini_options]
addopts = "-m 'not live'"
markers = [
    "live: test makes real network/API calls (deselected by default; run with -m live)",
]
testpaths = ["tests"]
```

- [ ] **Step 4: Create `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
.pytest_cache/
*.egg-info/
.venv/

# Worktrees / SDD scratch
.worktrees/
.superpowers/

# uv
# (uv.lock IS committed — do not ignore it)

# Environment
.env

# Data artifacts
data/*.db
data/*.db-journal
data/*.parquet
```

- [ ] **Step 5: Create `.env.example`**

```
# Copy to .env and fill in. .env is git-ignored.
ANTHROPIC_API_KEY=sk-ant-...
```

- [ ] **Step 6: Create `README.md`**

```markdown
# NFL Chat-With-Your-Database

Two-stage natural-language-to-SQL pipeline over NFL statistics. Stage 1
(Claude Haiku 4.5) writes SQL and self-corrects on execution errors.
Stage 2 (Claude Sonnet 5) checks that the SQL actually answers the
question. See `docs/superpowers/specs/2026-09-01-nfl-chat-db-design.md`.

## Setup

```bash
uv sync
cp .env.example .env   # then edit .env
```

## Ingest data (one-time, ~minutes, pulls from nflverse)

```bash
uv run python -m nfl_chatdb.ingest
```

## Ask a question

```bash
uv run nfl-chatdb "How many rushing touchdowns did Derrick Henry score in 2023?"
```

## Test

```bash
uv run pytest            # unit tests (no network)
uv run pytest -m live    # live API + ingestion tests (costs money, needs data/nfl.db)
```
```

- [ ] **Step 7: Create `src/nfl_chatdb/__init__.py`**

```python
"""NFL chat-with-your-database: two-stage NL-to-SQL pipeline."""

__version__ = "0.1.0"
```

- [ ] **Step 8: Create `tests/__init__.py`**

```python
```

(empty file)

- [ ] **Step 9: Create `tests/conftest.py`**

```python
"""Shared test fixtures."""

from __future__ import annotations

import sqlite3

import pytest

# A tiny schema snapshot string used by Stage 1 / Stage 2 / pipeline tests
# so they do not depend on a real ingested database.
FAKE_SCHEMA_TEXT = """\
Table: play_by_play
  play_id (INTEGER)
  game_id (TEXT)
  season (INTEGER)
  week (INTEGER)
  posteam (TEXT)
  rusher_player_name (TEXT)
  rush_touchdown (INTEGER)
  yards_gained (INTEGER)

Table: seasonal_stats
  player_id (TEXT)
  player_display_name (TEXT)
  season (INTEGER)
  rushing_tds (INTEGER)
  rushing_yards (INTEGER)

Table: snap_counts
  pfr_player_id (TEXT)
  player (TEXT)
  season (INTEGER)
  week (INTEGER)
  offense_snaps (INTEGER)
"""


@pytest.fixture
def fake_schema_text() -> str:
    return FAKE_SCHEMA_TEXT


@pytest.fixture
def tiny_db(tmp_path):
    """A minimal on-disk SQLite db matching FAKE_SCHEMA_TEXT's play_by_play."""
    path = tmp_path / "tiny.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE play_by_play (
            play_id INTEGER, game_id TEXT, season INTEGER, week INTEGER,
            posteam TEXT, rusher_player_name TEXT, rush_touchdown INTEGER,
            yards_gained INTEGER
        );
        INSERT INTO play_by_play VALUES
            (1, '2023_01_A_B', 2023, 1, 'TEN', 'D.Henry', 1, 12),
            (2, '2023_01_A_B', 2023, 1, 'TEN', 'D.Henry', 0, 4),
            (3, '2023_02_A_C', 2023, 2, 'TEN', 'D.Henry', 1, 3),
            (4, '2023_03_A_D', 2023, 3, 'TEN', 'D.Henry', 0, 5),
            (5, '2023_04_A_E', 2023, 4, 'TEN', 'D.Henry', 0, 8);
        """
    )
    conn.commit()
    conn.close()
    return path
```

- [ ] **Step 10: Create `tests/test_smoke.py`**

```python
from nfl_chatdb import __version__


def test_package_imports_and_has_version():
    assert __version__ == "0.1.0"
```

- [ ] **Step 11: Sync the environment**

Run: `uv sync`
Expected: creates `.venv/` and `uv.lock`, installs `anthropic`, `nfl-data-py`, `pydantic`, `pytest`.

- [ ] **Step 12: Run the smoke test**

Run: `uv run pytest -q`
Expected: PASS (1 passed). `uv sync` will download a managed CPython 3.11 if one is not already present — that is expected. If `nfl-data-py` still fails to resolve or build, confirm `uv run python --version` reports 3.11.x before investigating further.

- [ ] **Step 13: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore .env.example README.md src tests
git commit -m "chore: scaffold nfl_chatdb package with uv"
```

---

### Task 2: Read-only database access

**Files:**
- Create: `src/nfl_chatdb/database.py`
- Create: `tests/test_database.py`

**Interfaces:**
- Consumes: `tiny_db` fixture from `tests/conftest.py`.
- Produces:
  - `nfl_chatdb.database.DEFAULT_DB_PATH: pathlib.Path` — `Path("data/nfl.db")`.
  - `nfl_chatdb.database.QueryError(Exception)` — raised for non-SELECT statements and for SQLite execution errors; `str(err)` is the underlying message.
  - `nfl_chatdb.database.QueryResult` — dataclass with fields `columns: list[str]`, `rows: list[tuple]`, `row_count: int`. `row_count == len(rows)`.
  - `nfl_chatdb.database.connect(db_path: pathlib.Path = DEFAULT_DB_PATH) -> sqlite3.Connection` — opens read-only (`file:...?mode=ro`, `uri=True`). Raises `QueryError` if the file does not exist.
  - `nfl_chatdb.database.run_query(conn: sqlite3.Connection, sql: str) -> QueryResult` — executes a single read-only statement. Raises `QueryError` on: empty SQL, more than one statement, a leading keyword other than `SELECT` or `WITH`, or any `sqlite3.Error`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database.py
import sqlite3

import pytest

from nfl_chatdb.database import QueryError, QueryResult, connect, run_query


def test_run_query_returns_rows_and_count(tiny_db):
    conn = connect(tiny_db)
    result = run_query(
        conn,
        "SELECT rusher_player_name, SUM(rush_touchdown) AS tds "
        "FROM play_by_play WHERE season = 2023 GROUP BY rusher_player_name",
    )
    assert isinstance(result, QueryResult)
    assert result.columns == ["rusher_player_name", "tds"]
    assert result.rows == [("D.Henry", 2)]
    assert result.row_count == 1


def test_run_query_rejects_non_select(tiny_db):
    conn = connect(tiny_db)
    with pytest.raises(QueryError):
        run_query(conn, "DROP TABLE play_by_play")


def test_run_query_rejects_multiple_statements(tiny_db):
    conn = connect(tiny_db)
    with pytest.raises(QueryError):
        run_query(conn, "SELECT 1; SELECT 2")


def test_run_query_allows_with_cte(tiny_db):
    conn = connect(tiny_db)
    result = run_query(
        conn,
        "WITH x AS (SELECT * FROM play_by_play WHERE season = 2023) "
        "SELECT COUNT(*) AS n FROM x",
    )
    assert result.rows == [(5,)]


def test_run_query_wraps_sqlite_errors(tiny_db):
    conn = connect(tiny_db)
    with pytest.raises(QueryError) as excinfo:
        run_query(conn, "SELECT no_such_column FROM play_by_play")
    assert "no_such_column" in str(excinfo.value)


def test_connect_missing_file_raises(tmp_path):
    with pytest.raises(QueryError):
        connect(tmp_path / "does_not_exist.db")


def test_connection_is_read_only(tiny_db):
    conn = connect(tiny_db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO play_by_play (play_id) VALUES (99)")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_database.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nfl_chatdb.database'`

- [ ] **Step 3: Write the implementation**

```python
# src/nfl_chatdb/database.py
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_database.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/nfl_chatdb/database.py tests/test_database.py
git commit -m "feat: read-only SQLite query execution"
```

---

### Task 3: Data ingestion + schema snapshot

**Files:**
- Create: `src/nfl_chatdb/ingest.py`
- Create: `src/nfl_chatdb/schema_snapshot.txt` (generated by this task; committed)
- Create: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `nfl_chatdb.database.DEFAULT_DB_PATH`.
- Produces:
  - `nfl_chatdb.ingest.SEASONS: list[int]` — `[2021, 2022, 2023, 2024, 2025]`.
  - `nfl_chatdb.ingest.TABLES: tuple[str, ...]` — `("play_by_play", "seasonal_stats", "snap_counts")`.
  - `nfl_chatdb.ingest.SCHEMA_SNAPSHOT_PATH: pathlib.Path` — `Path(__file__).parent / "schema_snapshot.txt"`.
  - `nfl_chatdb.ingest.write_dataframe(df, table: str, conn: sqlite3.Connection) -> int` — writes `df` to `table` with `if_exists="replace"`; returns the row count written.
  - `nfl_chatdb.ingest.render_schema_snapshot(conn: sqlite3.Connection) -> str` — reads `PRAGMA table_info` for every table in `TABLES` and returns the snapshot text in the format shown in `tests/conftest.py::FAKE_SCHEMA_TEXT` (`Table: <name>\n  <col> (<type>)\n...` blocks separated by a blank line).
  - `nfl_chatdb.ingest.ingest(db_path=DEFAULT_DB_PATH, seasons=SEASONS) -> dict[str, int]` — pulls the three datasets via `nfl_data_py`, writes each to SQLite, regenerates `SCHEMA_SNAPSHOT_PATH`, and returns `{table: row_count}`.
  - `nfl_chatdb.ingest.main(argv=None) -> int` — argparse CLI (`--seasons`, `--db`); calls `ingest`, prints the row counts, returns 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest.py
import sqlite3

import pandas as pd
import pytest

from nfl_chatdb.ingest import (
    SEASONS,
    TABLES,
    render_schema_snapshot,
    write_dataframe,
)


def test_seasons_are_last_five_completed():
    assert SEASONS == [2021, 2022, 2023, 2024, 2025]


def test_tables_constant():
    assert TABLES == ("play_by_play", "seasonal_stats", "snap_counts")


def test_write_dataframe_replaces_and_counts(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    n = write_dataframe(df, "play_by_play", conn)
    assert n == 3
    # replace semantics: writing again does not append
    n2 = write_dataframe(df, "play_by_play", conn)
    assert n2 == 3
    assert conn.execute("SELECT COUNT(*) FROM play_by_play").fetchone()[0] == 3


def test_render_schema_snapshot_format(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(
        """
        CREATE TABLE play_by_play (play_id INTEGER, game_id TEXT);
        CREATE TABLE seasonal_stats (player_id TEXT, rushing_tds INTEGER);
        CREATE TABLE snap_counts (pfr_player_id TEXT, offense_snaps INTEGER);
        """
    )
    snapshot = render_schema_snapshot(conn)
    assert "Table: play_by_play" in snapshot
    assert "  play_id (INTEGER)" in snapshot
    assert "  game_id (TEXT)" in snapshot
    assert "Table: seasonal_stats" in snapshot
    assert "Table: snap_counts" in snapshot
    # blocks separated by a blank line
    assert "\n\nTable: seasonal_stats" in snapshot


@pytest.mark.live
def test_ingest_end_to_end(tmp_path):
    """Real pull from nflverse for a single season — slow, network."""
    from nfl_chatdb.ingest import ingest

    counts = ingest(db_path=tmp_path / "nfl.db", seasons=[2023])
    assert counts["play_by_play"] > 40000  # ~48k plays in a season
    assert counts["seasonal_stats"] > 500
    assert counts["snap_counts"] > 5000
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ingest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nfl_chatdb.ingest'`

- [ ] **Step 3: Write the implementation**

```python
# src/nfl_chatdb/ingest.py
"""One-time ingestion of nflverse datasets into local SQLite.

Also regenerates the committed schema snapshot that Stage 1 / Stage 2
load into their prompts. Re-run whenever the season list changes.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from nfl_chatdb.database import DEFAULT_DB_PATH

SEASONS = [2021, 2022, 2023, 2024, 2025]
TABLES = ("play_by_play", "seasonal_stats", "snap_counts")
SCHEMA_SNAPSHOT_PATH = Path(__file__).parent / "schema_snapshot.txt"


def write_dataframe(df, table: str, conn: sqlite3.Connection) -> int:
    df.to_sql(table, conn, if_exists="replace", index=False)
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def render_schema_snapshot(conn: sqlite3.Connection) -> str:
    blocks: list[str] = []
    for table in TABLES:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        lines = [f"Table: {table}"]
        for _cid, name, col_type, *_rest in rows:
            col_type = col_type or "?"
            lines.append(f"  {name} ({col_type})")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _load_datasets(seasons: list[int]):
    """Return {table_name: DataFrame} pulled from nfl_data_py."""
    import nfl_data_py as nfl

    return {
        "play_by_play": nfl.import_pbp_data(seasons, downcast=True, cache=False),
        "seasonal_stats": nfl.import_seasonal_data(seasons),
        "snap_counts": nfl.import_snap_counts(seasons),
    }


def ingest(
    db_path: Path = DEFAULT_DB_PATH, seasons: list[int] = SEASONS
) -> dict[str, int]:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    datasets = _load_datasets(seasons)
    conn = sqlite3.connect(db_path)
    try:
        counts = {
            table: write_dataframe(datasets[table], table, conn)
            for table in TABLES
        }
        conn.commit()
        SCHEMA_SNAPSHOT_PATH.write_text(render_schema_snapshot(conn))
    finally:
        conn.close()
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ingest nflverse data into SQLite")
    parser.add_argument(
        "--seasons",
        type=int,
        nargs="+",
        default=SEASONS,
        help="Seasons to ingest (default: last 5 completed).",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args(argv)
    counts = ingest(db_path=args.db, seasons=args.seasons)
    for table, count in counts.items():
        print(f"{table}: {count:,} rows")
    print(f"schema snapshot written to {SCHEMA_SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the non-live tests to verify they pass**

Run: `uv run pytest tests/test_ingest.py -q`
Expected: PASS (4 passed, 1 deselected)

- [ ] **Step 5: Run the real ingestion**

Run: `uv run python -m nfl_chatdb.ingest`
Expected: prints row counts for all three tables (play_by_play in the hundreds of thousands for 5 seasons) and "schema snapshot written to ...". Creates `data/nfl.db` (git-ignored, may be ~1 GB) and writes `src/nfl_chatdb/schema_snapshot.txt`.

If `nfl_data_py` raises on `import_seasonal_data` / `import_snap_counts` signature (the library's API has drifted across versions), check the installed version's signatures with `uv run python -c "import nfl_data_py, inspect; print(inspect.signature(nfl_data_py.import_seasonal_data)); print(inspect.signature(nfl_data_py.import_snap_counts))"` and adjust `_load_datasets` accordingly. Keep the call to `import_pbp_data(seasons, downcast=True, cache=False)` unless it errors.

- [ ] **Step 6: Verify the live ingestion test**

Run: `uv run pytest tests/test_ingest.py -m live -q`
Expected: PASS (1 passed) — takes 30–90s (pulls 2023 only).

- [ ] **Step 7: Commit**

```bash
git add src/nfl_chatdb/ingest.py src/nfl_chatdb/schema_snapshot.txt tests/test_ingest.py
git commit -m "feat: nflverse ingestion and schema snapshot generation"
```

---

### Task 4: Schema loading for prompts

**Files:**
- Create: `src/nfl_chatdb/schema.py`
- Create: `tests/test_schema.py`

**Interfaces:**
- Consumes: `nfl_chatdb.ingest.SCHEMA_SNAPSHOT_PATH` and the committed `src/nfl_chatdb/schema_snapshot.txt` from Task 3.
- Produces:
  - `nfl_chatdb.schema.load_schema_text(path: pathlib.Path | None = None) -> str` — reads the snapshot file (default `SCHEMA_SNAPSHOT_PATH`); raises `FileNotFoundError` with a message telling the user to run `python -m nfl_chatdb.ingest` if it is missing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema.py
import pytest

from nfl_chatdb.schema import load_schema_text


def test_load_schema_text_reads_committed_snapshot():
    text = load_schema_text()
    assert "Table: play_by_play" in text
    assert "Table: seasonal_stats" in text
    assert "Table: snap_counts" in text


def test_load_schema_text_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError) as excinfo:
        load_schema_text(tmp_path / "nope.txt")
    assert "nfl_chatdb.ingest" in str(excinfo.value)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nfl_chatdb.schema'`

- [ ] **Step 3: Write the implementation**

```python
# src/nfl_chatdb/schema.py
"""Load the static schema snapshot that Stage 1 and Stage 2 embed in prompts."""

from __future__ import annotations

from pathlib import Path

from nfl_chatdb.ingest import SCHEMA_SNAPSHOT_PATH


def load_schema_text(path: Path | None = None) -> str:
    path = Path(path) if path is not None else SCHEMA_SNAPSHOT_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"schema snapshot not found at {path}. "
            "Run `uv run python -m nfl_chatdb.ingest` to generate it."
        )
    return path.read_text()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_schema.py -q`
Expected: PASS (2 passed). If `test_load_schema_text_reads_committed_snapshot` fails with `FileNotFoundError`, Task 3 Step 5 was not run — run it first.

- [ ] **Step 5: Commit**

```bash
git add src/nfl_chatdb/schema.py tests/test_schema.py
git commit -m "feat: schema snapshot loader"
```

---

### Task 5: Result-sample formatting

**Files:**
- Create: `src/nfl_chatdb/formatting.py`
- Create: `tests/test_formatting.py`

**Interfaces:**
- Consumes: `nfl_chatdb.database.QueryResult`.
- Produces:
  - `nfl_chatdb.formatting.format_result_sample(result: QueryResult, max_rows: int = 8) -> str` — a compact text rendering for Stage 2's prompt and for CLI display: a header line `"<n> row(s). Showing first <k>:"` (or `"<n> row(s)."` when `n <= max_rows`, or `"0 rows."` when empty), followed by a pipe-delimited header row and up to `max_rows` pipe-delimited data rows. `None` values render as `NULL`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_formatting.py
from nfl_chatdb.database import QueryResult
from nfl_chatdb.formatting import format_result_sample


def test_format_small_result():
    r = QueryResult(columns=["name", "tds"], rows=[("D.Henry", 12)], row_count=1)
    out = format_result_sample(r)
    assert out.splitlines()[0] == "1 row(s)."
    assert "name | tds" in out
    assert "D.Henry | 12" in out


def test_format_empty_result():
    r = QueryResult(columns=["name"], rows=[], row_count=0)
    assert format_result_sample(r).splitlines()[0] == "0 rows."


def test_format_truncates_and_reports_total():
    rows = [(f"p{i}", i) for i in range(20)]
    r = QueryResult(columns=["name", "n"], rows=rows, row_count=20)
    out = format_result_sample(r, max_rows=5)
    assert out.splitlines()[0] == "20 row(s). Showing first 5:"
    # header + 5 data rows + the header line
    assert len(out.splitlines()) == 7
    assert "p4 | 4" in out
    assert "p5 | 5" not in out


def test_format_renders_null():
    r = QueryResult(columns=["a", "b"], rows=[(1, None)], row_count=1)
    assert "1 | NULL" in format_result_sample(r)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_formatting.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nfl_chatdb.formatting'`

- [ ] **Step 3: Write the implementation**

```python
# src/nfl_chatdb/formatting.py
"""Compact text rendering of query results for prompts and CLI output."""

from __future__ import annotations

from nfl_chatdb.database import QueryResult


def _cell(value) -> str:
    return "NULL" if value is None else str(value)


def format_result_sample(result: QueryResult, max_rows: int = 8) -> str:
    if result.row_count == 0:
        return "0 rows."

    if result.row_count <= max_rows:
        header_line = f"{result.row_count} row(s)."
        shown = result.rows
    else:
        header_line = f"{result.row_count} row(s). Showing first {max_rows}:"
        shown = result.rows[:max_rows]

    lines = [header_line, " | ".join(result.columns)]
    lines.extend(" | ".join(_cell(v) for v in row) for row in shown)
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_formatting.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/nfl_chatdb/formatting.py tests/test_formatting.py
git commit -m "feat: result-sample formatting"
```

---

### Task 6: Stage 1 — SQL generation with execution-retry loop

**Files:**
- Create: `src/nfl_chatdb/stage1_sql.py`
- Create: `tests/test_stage1_sql.py`

**Interfaces:**
- Consumes: `nfl_chatdb.database.{QueryError, QueryResult, run_query}`, the `fake_schema_text` and `tiny_db` fixtures.
- Produces:
  - `nfl_chatdb.stage1_sql.STAGE1_MODEL: str` — `"claude-haiku-4-5"`.
  - `nfl_chatdb.stage1_sql.Stage1Result` — dataclass: `sql: str`, `result: QueryResult`, `attempts: int`, `degenerate: bool` (True when the final result executed cleanly but is empty).
  - `nfl_chatdb.stage1_sql.Stage1Error(Exception)` — raised when every attempt ended in a `QueryError`; attributes `last_sql: str`, `last_error: str`.
  - `nfl_chatdb.stage1_sql.extract_sql(text: str) -> str` — pulls SQL from a model reply: if a ```` ```sql ```` (or bare ```` ``` ````) fence is present, return its contents; otherwise return the stripped text. Raises `Stage1Error` (with `last_sql=""`) if the result is empty.
  - `nfl_chatdb.stage1_sql.generate_sql(client, question: str, schema_text: str, conn, *, correction: str | None = None, max_attempts: int = 2) -> Stage1Result` — the hand-rolled loop. `client` is any object with `.messages.create(...)` returning an object whose `.content` is a list of blocks with `.type` / `.text` (the real `anthropic.Anthropic` client, or a fake in tests). `correction`, when given, is appended to the first user message as a targeted instruction from Stage 2.

**Loop behaviour (implement exactly):**
1. Build `messages` = one user turn containing the question, the schema, and (if `correction`) the correction instruction.
2. For attempt in `1..max_attempts`:
   a. Call `client.messages.create(model=STAGE1_MODEL, max_tokens=1024, system=SYSTEM_PROMPT, messages=messages)`.
   b. `sql = extract_sql(<concatenated text blocks>)`.
   c. Try `result = run_query(conn, sql)`.
      - On `QueryError` as `err`: if this was the last attempt, raise `Stage1Error(last_sql=sql, last_error=str(err))`. Otherwise append the assistant reply and a user turn `f"That query failed with: {err}\nReturn a corrected SQL query."` and continue.
      - On success with `result.row_count == 0`: if this was the last attempt, return `Stage1Result(sql, result, attempt, degenerate=True)`. Otherwise append the assistant reply and a user turn `"That query returned no rows. If that seems wrong, return a corrected SQL query; otherwise return the same query."` and continue.
      - On success with rows: return `Stage1Result(sql, result, attempt, degenerate=False)`.
3. (Unreachable — the loop always returns or raises.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stage1_sql.py
import pytest

from nfl_chatdb.database import connect
from nfl_chatdb.stage1_sql import (
    STAGE1_MODEL,
    Stage1Error,
    Stage1Result,
    extract_sql,
    generate_sql,
)


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeMessages:
    """Records calls; replays a scripted list of reply strings."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self._replies.pop(0))


class FakeClient:
    def __init__(self, replies):
        self.messages = FakeMessages(replies)


def test_model_constant():
    assert STAGE1_MODEL == "claude-haiku-4-5"


def test_extract_sql_from_fence():
    assert extract_sql("here you go:\n```sql\nSELECT 1\n```\n") == "SELECT 1"


def test_extract_sql_bare():
    assert extract_sql("  SELECT 1  ") == "SELECT 1"


def test_extract_sql_empty_raises():
    with pytest.raises(Stage1Error):
        extract_sql("   ")


def test_generate_sql_succeeds_first_try(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = FakeClient(
        ["```sql\nSELECT SUM(rush_touchdown) AS tds FROM play_by_play "
         "WHERE season = 2023\n```"]
    )
    out = generate_sql(client, "How many rushing TDs in 2023?", fake_schema_text, conn)
    assert isinstance(out, Stage1Result)
    assert out.attempts == 1
    assert out.degenerate is False
    assert out.result.rows == [(2,)]
    assert client.messages.calls[0]["model"] == STAGE1_MODEL


def test_generate_sql_retries_on_execution_error(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = FakeClient(
        [
            "```sql\nSELECT SUM(bad_col) FROM play_by_play\n```",
            "```sql\nSELECT SUM(rush_touchdown) AS tds FROM play_by_play\n```",
        ]
    )
    out = generate_sql(client, "rushing TDs", fake_schema_text, conn)
    assert out.attempts == 2
    assert out.result.rows == [(2,)]
    # the retry turn included the sqlite error text
    retry_msgs = client.messages.calls[1]["messages"]
    assert any("bad_col" in str(m["content"]) for m in retry_msgs)


def test_generate_sql_raises_after_exhausting_attempts(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = FakeClient(
        [
            "```sql\nSELECT bad_a FROM play_by_play\n```",
            "```sql\nSELECT bad_b FROM play_by_play\n```",
        ]
    )
    with pytest.raises(Stage1Error) as excinfo:
        generate_sql(client, "q", fake_schema_text, conn)
    assert excinfo.value.last_sql == "SELECT bad_b FROM play_by_play"
    assert "bad_b" in excinfo.value.last_error


def test_generate_sql_empty_result_retries_then_returns_degenerate(
    tiny_db, fake_schema_text
):
    conn = connect(tiny_db)
    client = FakeClient(
        [
            "```sql\nSELECT * FROM play_by_play WHERE season = 1999\n```",
            "```sql\nSELECT * FROM play_by_play WHERE season = 1998\n```",
        ]
    )
    out = generate_sql(client, "q", fake_schema_text, conn)
    assert out.attempts == 2
    assert out.degenerate is True
    assert out.result.row_count == 0


def test_generate_sql_passes_correction_into_prompt(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = FakeClient(
        ["```sql\nSELECT SUM(rush_touchdown) AS tds FROM play_by_play\n```"]
    )
    generate_sql(
        client, "q", fake_schema_text, conn,
        correction="Only count rushing TDs, not receiving TDs.",
    )
    first_msgs = client.messages.calls[0]["messages"]
    assert any("receiving TDs" in str(m["content"]) for m in first_msgs)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_stage1_sql.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nfl_chatdb.stage1_sql'`

- [ ] **Step 3: Write the implementation**

```python
# src/nfl_chatdb/stage1_sql.py
"""Stage 1: generate SQL from a question and self-correct on execution errors."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nfl_chatdb.database import QueryError, QueryResult, run_query

STAGE1_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = (
    "You translate questions about NFL statistics into a single SQLite "
    "SELECT query. Use only the tables and columns in the provided schema. "
    "Return only the SQL, in a ```sql fenced block, with no explanation. "
    "The query must be a single read-only SELECT (a leading WITH is allowed)."
)

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


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
            max_tokens=1024,
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_stage1_sql.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/nfl_chatdb/stage1_sql.py tests/test_stage1_sql.py
git commit -m "feat: Stage 1 SQL generation with execution-retry loop"
```

---

### Task 7: Stage 2 — semantic validation (pure function)

**Files:**
- Create: `src/nfl_chatdb/stage2_validate.py`
- Create: `tests/test_stage2_validate.py`

**Interfaces:**
- Consumes: the `fake_schema_text` fixture.
- Produces:
  - `nfl_chatdb.stage2_validate.STAGE2_MODEL: str` — `"claude-sonnet-5"`.
  - `nfl_chatdb.stage2_validate.Stage2Verdict` — a `pydantic.BaseModel` with fields `valid: bool`, `issues: list[str]` (default `[]`), `suggested_fix: str | None` (default `None`).
  - `nfl_chatdb.stage2_validate.validate_semantics(client, question: str, sql: str, schema_text: str, result_sample: str) -> Stage2Verdict` — one `client.messages.parse(...)` call with `output_format=Stage2Verdict`; returns `response.parsed_output`. `client` is any object exposing `.messages.parse(...)` (the real client, or a fake in tests).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stage2_validate.py
from nfl_chatdb.stage2_validate import (
    STAGE2_MODEL,
    Stage2Verdict,
    validate_semantics,
)


class _ParseResponse:
    def __init__(self, verdict):
        self.parsed_output = verdict


class FakeParseMessages:
    def __init__(self, verdict):
        self._verdict = verdict
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _ParseResponse(self._verdict)


class FakeParseClient:
    def __init__(self, verdict):
        self.messages = FakeParseMessages(verdict)


def test_model_constant():
    assert STAGE2_MODEL == "claude-sonnet-5"


def test_verdict_defaults():
    v = Stage2Verdict(valid=True)
    assert v.issues == []
    assert v.suggested_fix is None


def test_validate_semantics_returns_parsed_verdict(fake_schema_text):
    verdict = Stage2Verdict(
        valid=False,
        issues=["Counts all TDs, not just rushing TDs."],
        suggested_fix="Filter to rush_touchdown = 1.",
    )
    client = FakeParseClient(verdict)
    out = validate_semantics(
        client,
        question="How many rushing TDs did D.Henry score in 2023?",
        sql="SELECT COUNT(*) FROM play_by_play WHERE touchdown = 1",
        schema_text=fake_schema_text,
        result_sample="1 row(s).\ncnt\n30",
    )
    assert out is verdict
    call = client.messages.calls[0]
    assert call["model"] == STAGE2_MODEL
    assert call["output_format"] is Stage2Verdict
    # the prompt carries all four inputs
    sent = str(call["messages"])
    assert "rushing TDs" in sent
    assert "touchdown = 1" in sent
    assert "cnt" in sent
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_stage2_validate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nfl_chatdb.stage2_validate'`

- [ ] **Step 3: Write the implementation**

```python
# src/nfl_chatdb/stage2_validate.py
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
    return response.parsed_output
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_stage2_validate.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/nfl_chatdb/stage2_validate.py tests/test_stage2_validate.py
git commit -m "feat: Stage 2 semantic validation as a pure function"
```

---

### Task 8: Pipeline orchestration

**Files:**
- Create: `src/nfl_chatdb/pipeline.py`
- Create: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `stage1_sql.{generate_sql, Stage1Result, Stage1Error}`, `stage2_validate.{validate_semantics, Stage2Verdict}`, `formatting.format_result_sample`, `database.{connect, QueryResult}`, `schema.load_schema_text`.
- Produces:
  - `nfl_chatdb.pipeline.PipelineOutcome` — dataclass: `question: str`, `sql: str`, `result: QueryResult`, `verdict: Stage2Verdict`, `caveated: bool`, `semantic_retries: int`, `stage1_attempts: int`.
  - `nfl_chatdb.pipeline.answer_question(client, question: str, *, conn, schema_text: str, max_semantic_retries: int = 1) -> PipelineOutcome` — the end-to-end flow.

**Flow (implement exactly):**
1. `s1 = generate_sql(client, question, schema_text, conn)`.
2. `sample = format_result_sample(s1.result)`.
3. `verdict = validate_semantics(client, question, s1.sql, schema_text, sample)`.
4. `retries = 0`.
5. While `not verdict.valid` and `retries < max_semantic_retries`:
   a. `retries += 1`.
   b. `correction = verdict.suggested_fix or "; ".join(verdict.issues)`.
   c. `s1 = generate_sql(client, question, schema_text, conn, correction=correction)`.
   d. `sample = format_result_sample(s1.result)`.
   e. `verdict = validate_semantics(client, question, s1.sql, schema_text, sample)`.
6. Return `PipelineOutcome(question, s1.sql, s1.result, verdict, caveated=not verdict.valid, semantic_retries=retries, stage1_attempts=s1.attempts)`.

`Stage1Error` propagates out of `answer_question` unchanged (the CLI handles it).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
import pytest

from nfl_chatdb.database import connect
from nfl_chatdb.pipeline import PipelineOutcome, answer_question
from nfl_chatdb.stage2_validate import Stage2Verdict


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _CreateResponse:
    def __init__(self, text):
        self.content = [_Block(text)]


class _ParseResponse:
    def __init__(self, verdict):
        self.parsed_output = verdict


class ScriptedClient:
    """Replays scripted `messages.create` and `messages.parse` results."""

    def __init__(self, create_replies, verdicts):
        self._create = list(create_replies)
        self._verdicts = list(verdicts)
        self.create_calls = []
        self.parse_calls = []
        client = self

        class _M:
            def create(self, **kwargs):
                client.create_calls.append(kwargs)
                return _CreateResponse(client._create.pop(0))

            def parse(self, **kwargs):
                client.parse_calls.append(kwargs)
                return _ParseResponse(client._verdicts.pop(0))

        self.messages = _M()


SQL_OK = "```sql\nSELECT SUM(rush_touchdown) AS tds FROM play_by_play WHERE season = 2023\n```"
SQL_ALL_TD = "```sql\nSELECT COUNT(*) AS tds FROM play_by_play WHERE season = 2023\n```"


def test_valid_first_pass(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_OK],
        verdicts=[Stage2Verdict(valid=True)],
    )
    out = answer_question(
        client, "How many rushing TDs in 2023?",
        conn=conn, schema_text=fake_schema_text,
    )
    assert isinstance(out, PipelineOutcome)
    assert out.caveated is False
    assert out.semantic_retries == 0
    assert out.result.rows == [(2,)]


def test_semantic_retry_then_valid(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_ALL_TD, SQL_OK],
        verdicts=[
            Stage2Verdict(
                valid=False,
                issues=["Counts all plays, not TDs."],
                suggested_fix="Sum rush_touchdown instead of COUNT(*).",
            ),
            Stage2Verdict(valid=True),
        ],
    )
    out = answer_question(
        client, "rushing TDs in 2023", conn=conn, schema_text=fake_schema_text,
    )
    assert out.semantic_retries == 1
    assert out.caveated is False
    assert out.result.rows == [(2,)]
    # the correction reached Stage 1's second prompt
    second = str(client.create_calls[1]["messages"])
    assert "rush_touchdown" in second


def test_still_invalid_after_retry_is_caveated(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    bad_verdict = Stage2Verdict(
        valid=False, issues=["Still wrong."], suggested_fix="Try harder."
    )
    client = ScriptedClient(
        create_replies=[SQL_ALL_TD, SQL_ALL_TD],
        verdicts=[bad_verdict, bad_verdict],
    )
    out = answer_question(
        client, "rushing TDs", conn=conn, schema_text=fake_schema_text,
    )
    assert out.semantic_retries == 1
    assert out.caveated is True
    assert out.verdict.valid is False


def test_stage1_error_propagates(tiny_db, fake_schema_text):
    from nfl_chatdb.stage1_sql import Stage1Error

    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[
            "```sql\nSELECT bad_a FROM play_by_play\n```",
            "```sql\nSELECT bad_b FROM play_by_play\n```",
        ],
        verdicts=[],
    )
    with pytest.raises(Stage1Error):
        answer_question(
            client, "q", conn=conn, schema_text=fake_schema_text,
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nfl_chatdb.pipeline'`

- [ ] **Step 3: Write the implementation**

```python
# src/nfl_chatdb/pipeline.py
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/nfl_chatdb/pipeline.py tests/test_pipeline.py
git commit -m "feat: pipeline orchestration with single semantic retry"
```

---

### Task 9: CLI entry point

**Files:**
- Create: `src/nfl_chatdb/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `pipeline.answer_question`, `database.connect`, `schema.load_schema_text`, `stage1_sql.Stage1Error`, `formatting.format_result_sample`.
- Produces:
  - `nfl_chatdb.cli.build_client() -> anthropic.Anthropic` — thin wrapper returning `anthropic.Anthropic()` (reads `ANTHROPIC_API_KEY` from env). Factored out so tests can monkeypatch it.
  - `nfl_chatdb.cli.render_outcome(outcome: PipelineOutcome) -> str` — the human-readable report: the SQL, the result sample, and — when `outcome.caveated` — a `"⚠ Caveat:"` block listing `outcome.verdict.issues`.
  - `nfl_chatdb.cli.main(argv=None) -> int` — argparse: positional `question`, `--db` (default `DEFAULT_DB_PATH`), `--json` (dump `outcome` as JSON instead of the report). Returns `0` on success, `1` on `Stage1Error` (after printing the error), `2` on a missing database / schema snapshot (`QueryError` / `FileNotFoundError`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import json

import pytest

from nfl_chatdb import cli
from nfl_chatdb.database import QueryResult, connect
from nfl_chatdb.pipeline import PipelineOutcome
from nfl_chatdb.stage1_sql import Stage1Error
from nfl_chatdb.stage2_validate import Stage2Verdict


def _outcome(caveated=False):
    return PipelineOutcome(
        question="q",
        sql="SELECT 1 AS n",
        result=QueryResult(columns=["n"], rows=[(1,)], row_count=1),
        verdict=Stage2Verdict(
            valid=not caveated,
            issues=["Only counts rushing TDs."] if caveated else [],
        ),
        caveated=caveated,
        semantic_retries=1 if caveated else 0,
        stage1_attempts=1,
    )


def test_render_outcome_plain():
    text = cli.render_outcome(_outcome())
    assert "SELECT 1 AS n" in text
    assert "n" in text
    assert "Caveat" not in text


def test_render_outcome_caveated():
    text = cli.render_outcome(_outcome(caveated=True))
    assert "Caveat" in text
    assert "Only counts rushing TDs." in text


def test_main_happy_path(monkeypatch, tiny_db, capsys):
    monkeypatch.setattr(cli, "build_client", lambda: object())
    monkeypatch.setattr(cli, "load_schema_text", lambda: "Table: play_by_play")
    monkeypatch.setattr(
        cli, "answer_question", lambda *a, **k: _outcome()
    )
    rc = cli.main(["how many?", "--db", str(tiny_db)])
    assert rc == 0
    assert "SELECT 1 AS n" in capsys.readouterr().out


def test_main_json_output(monkeypatch, tiny_db, capsys):
    monkeypatch.setattr(cli, "build_client", lambda: object())
    monkeypatch.setattr(cli, "load_schema_text", lambda: "schema")
    monkeypatch.setattr(cli, "answer_question", lambda *a, **k: _outcome())
    rc = cli.main(["q", "--db", str(tiny_db), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sql"] == "SELECT 1 AS n"
    assert payload["caveated"] is False


def test_main_stage1_error_returns_1(monkeypatch, tiny_db, capsys):
    monkeypatch.setattr(cli, "build_client", lambda: object())
    monkeypatch.setattr(cli, "load_schema_text", lambda: "schema")

    def _boom(*a, **k):
        raise Stage1Error(last_sql="SELECT x", last_error="no such column: x")

    monkeypatch.setattr(cli, "answer_question", _boom)
    rc = cli.main(["q", "--db", str(tiny_db)])
    assert rc == 1
    assert "no such column: x" in capsys.readouterr().out


def test_main_missing_db_returns_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "build_client", lambda: object())
    rc = cli.main(["q", "--db", str(tmp_path / "absent.db")])
    assert rc == 2
    assert "ingest" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nfl_chatdb.cli'`

- [ ] **Step 3: Write the implementation**

```python
# src/nfl_chatdb/cli.py
"""Command-line interface: ask one question, print the answer."""

from __future__ import annotations

import argparse
import json

from nfl_chatdb.database import DEFAULT_DB_PATH, QueryError, connect
from nfl_chatdb.formatting import format_result_sample
from nfl_chatdb.pipeline import PipelineOutcome, answer_question
from nfl_chatdb.schema import load_schema_text
from nfl_chatdb.stage1_sql import Stage1Error


def build_client():
    import anthropic

    return anthropic.Anthropic()


def render_outcome(outcome: PipelineOutcome) -> str:
    lines = [
        "SQL:",
        f"  {outcome.sql}",
        "",
        format_result_sample(outcome.result),
    ]
    if outcome.caveated:
        lines += ["", "⚠ Caveat: this answer may not fully match the question."]
        lines += [f"  - {issue}" for issue in outcome.verdict.issues]
    return "\n".join(lines)


def _outcome_to_dict(outcome: PipelineOutcome) -> dict:
    return {
        "question": outcome.question,
        "sql": outcome.sql,
        "columns": outcome.result.columns,
        "rows": [list(r) for r in outcome.result.rows],
        "row_count": outcome.result.row_count,
        "caveated": outcome.caveated,
        "issues": outcome.verdict.issues,
        "semantic_retries": outcome.semantic_retries,
        "stage1_attempts": outcome.stage1_attempts,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Ask a natural-language question about NFL statistics."
    )
    parser.add_argument("question", help="The question to answer.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of a report."
    )
    args = parser.parse_args(argv)

    try:
        schema_text = load_schema_text()
        conn = connect(args.db)
    except (QueryError, FileNotFoundError) as err:
        print(f"{err}\nRun `uv run python -m nfl_chatdb.ingest` first.")
        return 2

    try:
        outcome = answer_question(
            build_client(), args.question, conn=conn, schema_text=schema_text
        )
    except Stage1Error as err:
        print(f"Could not produce a working query: {err.last_error}")
        return 1

    if args.json:
        print(json.dumps(_outcome_to_dict(outcome), indent=2))
    else:
        print(render_outcome(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Run the full unit suite**

Run: `uv run pytest -q`
Expected: PASS (all non-live tests; live deselected)

- [ ] **Step 6: Commit**

```bash
git add src/nfl_chatdb/cli.py tests/test_cli.py
git commit -m "feat: argparse CLI"
```

---

### Task 10: Live end-to-end smoke tests

**Files:**
- Create: `tests/test_live_pipeline.py`

**Interfaces:**
- Consumes: `nfl_chatdb.database.connect`, `nfl_chatdb.schema.load_schema_text`, `nfl_chatdb.cli.build_client`, `nfl_chatdb.pipeline.answer_question`. Requires `data/nfl.db` (from Task 3 Step 5) and `ANTHROPIC_API_KEY`.
- Produces: a small set of `@pytest.mark.live` end-to-end checks. This is the MVP-confidence smoke set described in the spec's Testing section — **not** the deferred formal adversarial eval.

- [ ] **Step 1: Write the live tests**

```python
# tests/test_live_pipeline.py
"""Live end-to-end smoke tests. Run with: uv run pytest -m live tests/test_live_pipeline.py

Requires data/nfl.db (uv run python -m nfl_chatdb.ingest) and ANTHROPIC_API_KEY.
"""

import os
from pathlib import Path

import pytest

from nfl_chatdb.cli import build_client
from nfl_chatdb.database import DEFAULT_DB_PATH, connect
from nfl_chatdb.pipeline import answer_question
from nfl_chatdb.schema import load_schema_text

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def conn():
    if not DEFAULT_DB_PATH.exists():
        pytest.skip("data/nfl.db not present; run `uv run python -m nfl_chatdb.ingest`")
    return connect(DEFAULT_DB_PATH)


@pytest.fixture(scope="module")
def schema_text():
    return load_schema_text()


@pytest.fixture(scope="module")
def client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    return build_client()


def test_simple_count_returns_sane_shape(client, conn, schema_text):
    out = answer_question(
        client,
        "How many passing touchdowns were thrown in the 2023 regular season?",
        conn=conn,
        schema_text=schema_text,
    )
    assert out.result.row_count >= 1
    # a single scalar count, in the plausible range for one season
    value = out.result.rows[0][0]
    assert isinstance(value, (int, float))
    assert 500 < value < 1200


def test_ranked_list_question_returns_multiple_rows(client, conn, schema_text):
    out = answer_question(
        client,
        "Which 5 players had the most rushing yards in the 2022 season?",
        conn=conn,
        schema_text=schema_text,
    )
    assert out.result.row_count >= 3
    assert not out.caveated


def test_pipeline_never_raises_on_a_vague_question(client, conn, schema_text):
    # Should still return something (possibly caveated), not blow up.
    out = answer_question(
        client, "Who was the best quarterback in 2023?",
        conn=conn, schema_text=schema_text,
    )
    assert out.sql.lower().lstrip().startswith(("select", "with"))
```

- [ ] **Step 2: Run the live tests**

Run: `uv run pytest -m live tests/test_live_pipeline.py -q`
Expected: PASS (3 passed) — makes real API calls (a few cents). If `data/nfl.db` or `ANTHROPIC_API_KEY` is absent, tests skip rather than fail. If a bound assertion (e.g. `500 < value < 1200`) is off because the schema uses a different column meaning, adjust the bound — the goal is "sane shape," not an exact number.

- [ ] **Step 3: Run the entire suite one final time**

Run: `uv run pytest -q` then `uv run pytest -m live -q`
Expected: unit suite green; live suite green (or skipped where prerequisites are missing).

- [ ] **Step 4: Commit**

```bash
git add tests/test_live_pipeline.py
git commit -m "test: live end-to-end smoke tests"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task(s) |
|---|---|
| Data Layer — three nflverse datasets via `nfl_data_py` → SQLite `to_sql()` | Task 3 |
| Ingestion is a one-time script, no ORM/migrations | Task 3 |
| Interface: CLI/script only | Task 9 |
| Single-turn only (no conversation memory) | Task 8 (`answer_question` is stateless), Global Constraints |
| Stage 1: static schema baked into system prompt (no `get_schema()` tool) | Task 4 + Task 6 (`schema_text` passed in, embedded in the prompt) |
| Stage 1: generate → execute → self-correct on error/degenerate, cap 2 attempts | Task 6 |
| Stage 1: read-only SELECTs only | Task 2 (`run_query` guard + `mode=ro`) |
| Stage 1 → Stage 2 handoff: final SQL + result sample (count + small sample) | Task 5 + Task 8 |
| Stage 2: single-shot, stronger model, forced structured output `{valid, issues, suggested_fix}` | Task 7 |
| Stage 2: result sample (not just SQL) to catch empty/absurd results | Task 5, Task 7 (`result_sample` input), Task 10 |
| End-to-end: valid → return; invalid → targeted correction back to Stage 1; one semantic retry | Task 8 |
| Still invalid after retry → annotate, don't block | Task 8 (`caveated`), Task 9 (`render_outcome` caveat block) |
| Tech stack: raw Anthropic API both stages, hand-rolled Stage 1 loop, no agent framework | Task 6, Task 7 |
| Cost/latency: schema pre-loaded, retry caps (2 / 1), cheap model Stage 1 / strong Stage 2 | Task 6, Task 7, Task 8, Global Constraints |
| Testing: Stage 2 as pure function with fixtures | Task 7 |
| Testing: ingestion sanity checks (columns/row counts) | Task 3 |
| Testing: handful of smoke-test questions end-to-end | Task 10 |
| Non-goal: no glossary/semantic layer | Global Constraints (enforced by omission) |
| Deferred: multi-turn, correction memory, formal adversarial eval, real UI, extra datasets | Not implemented (correctly) |

No gaps found.

**2. Placeholder scan:** No `TBD`/`TODO`/"add error handling"/"similar to Task N" present. Every code step has a full code block. Error handling is concrete (`QueryError`, `Stage1Error`, CLI exit codes 1/2).

**3. Type consistency:**
- `QueryResult(columns, rows, row_count)` — defined Task 2, used identically in Tasks 3, 5, 8, 9, 10.
- `Stage1Result(sql, result, attempts, degenerate)` — defined Task 6, consumed in Task 8 (`s1.sql`, `s1.result`, `s1.attempts`).
- `Stage1Error(last_sql, last_error)` — defined Task 6, caught in Tasks 8 (propagate), 9 (`err.last_error`).
- `Stage2Verdict(valid, issues, suggested_fix)` — defined Task 7, used in Tasks 8 (`verdict.valid`, `verdict.suggested_fix`, `verdict.issues`), 9 (`outcome.verdict.issues`).
- `PipelineOutcome(question, sql, result, verdict, caveated, semantic_retries, stage1_attempts)` — defined Task 8, used in Task 9 (`render_outcome`, `_outcome_to_dict`) and Task 10.
- `generate_sql(client, question, schema_text, conn, *, correction=None, max_attempts=2)` — signature identical in Task 6 definition and Task 8 calls.
- `validate_semantics(client, question, sql, schema_text, result_sample)` — identical Task 7 definition and Task 8 calls.
- `load_schema_text` — Task 4 defines `load_schema_text(path=None)`; Task 9 imports it into `cli` and monkeypatches `cli.load_schema_text` in tests (consistent).
- `answer_question(client, question, *, conn, schema_text, max_semantic_retries=1)` — Task 8 definition; Task 9 calls it with `conn=`, `schema_text=` kwargs; Task 10 same.

Consistent throughout.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-09-01-nfl-chat-db-mvp.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
