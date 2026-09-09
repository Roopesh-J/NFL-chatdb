# NFL ChatDB Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local pywebview desktop app wrapping the two-stage NL-to-SQL pipeline, with a single-question panel and an expandable "how this was answered" section.

**Architecture:** `pywebview` opens a native window rendering one self-contained HTML page. An `Api` object is exposed to the page via pywebview's `js_api` bridge; its `ask()` method opens a fresh read-only SQLite connection, runs `pipeline.answer_question`, and returns a serialized outcome dict. No HTTP server. A shared `formatting.outcome_to_dict` serializer is used by both the CLI and the app.

**Tech Stack:** Python 3.11, pywebview, existing `nfl_chatdb` package, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-09-nfl-chatdb-desktop-app-design.md`

## Global Constraints

- Python `>=3.11,<3.12` (`nfl_data_py` caps `pandas<2.0`).
- Package manager: `uv`. Run everything via `uv run`.
- Tests use pytest, live in `tests/`, reuse `tests/conftest.py` fixtures, and must not import `webview`.
- Existing pipeline entrypoint: `pipeline.answer_question(client, question, *, conn, schema_text, max_semantic_retries=1) -> PipelineOutcome`.
- `PipelineOutcome` fields: `question, sql, result (QueryResult), verdict (Stage2Verdict), caveated, semantic_retries, stage1_attempts`.
- `QueryResult` fields: `columns: list[str], rows: list[tuple], row_count: int, truncated: bool`.
- `Stage2Verdict` fields include: `valid: bool, issues: list[str]`.
- `database.connect(db_path=DEFAULT_DB_PATH) -> sqlite3.Connection` raises `database.QueryError` if the file is missing. `DEFAULT_DB_PATH = Path("data/nfl.db")`.
- `stage1_sql.Stage1Error` has attribute `last_error`.
- `cli.build_client()` runs `load_dotenv()` then `anthropic.Anthropic()` (raises `anthropic.AnthropicError` when no key).
- `schema.load_schema_text() -> str`.

---

### Task 1: Shared outcome serializer

**Files:**
- Modify: `src/nfl_chatdb/formatting.py` (add `outcome_to_dict`)
- Modify: `src/nfl_chatdb/cli.py` (remove `_outcome_to_dict`, import from formatting)
- Test: `tests/test_formatting.py`

**Interfaces:**
- Consumes: `PipelineOutcome` from `nfl_chatdb.pipeline`.
- Produces: `formatting.outcome_to_dict(outcome: PipelineOutcome) -> dict` with keys: `question, sql, columns, rows, row_count, truncated, caveated, issues, semantic_retries, stage1_attempts`. `rows` is a list of lists. `truncated` is `outcome.result.truncated`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_formatting.py`:

```python
from nfl_chatdb.formatting import outcome_to_dict
from nfl_chatdb.pipeline import PipelineOutcome
from nfl_chatdb.database import QueryResult
from nfl_chatdb.stage2_validate import Stage2Verdict


def _outcome(**over):
    base = dict(
        question="q",
        sql="SELECT 1",
        result=QueryResult(columns=["a"], rows=[(1,)], row_count=1, truncated=False),
        verdict=Stage2Verdict(valid=True, issues=[]),
        caveated=False,
        semantic_retries=0,
        stage1_attempts=1,
    )
    base.update(over)
    return PipelineOutcome(**base)


def test_outcome_to_dict_shape():
    d = outcome_to_dict(_outcome())
    assert d == {
        "question": "q",
        "sql": "SELECT 1",
        "columns": ["a"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": False,
        "caveated": False,
        "issues": [],
        "semantic_retries": 0,
        "stage1_attempts": 1,
    }


def test_outcome_to_dict_surfaces_truncated_and_issues():
    o = _outcome(
        result=QueryResult(columns=["a"], rows=[(1,)], row_count=1, truncated=True),
        verdict=Stage2Verdict(valid=False, issues=["wrong season"]),
        caveated=True,
    )
    d = outcome_to_dict(o)
    assert d["truncated"] is True
    assert d["issues"] == ["wrong season"]
    assert d["caveated"] is True
```

Check `Stage2Verdict`'s real constructor signature first (`src/nfl_chatdb/stage2_validate.py`) and adjust the `Stage2Verdict(...)` kwargs in the helper if needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_formatting.py -q`
Expected: FAIL — `ImportError: cannot import name 'outcome_to_dict'`.

- [ ] **Step 3: Write minimal implementation**

In `src/nfl_chatdb/formatting.py`, add (keep existing `format_result_sample`):

```python
def outcome_to_dict(outcome) -> dict:
    """Serialize a PipelineOutcome for JSON / the desktop app bridge."""
    return {
        "question": outcome.question,
        "sql": outcome.sql,
        "columns": outcome.result.columns,
        "rows": [list(r) for r in outcome.result.rows],
        "row_count": outcome.result.row_count,
        "truncated": outcome.result.truncated,
        "caveated": outcome.caveated,
        "issues": outcome.verdict.issues,
        "semantic_retries": outcome.semantic_retries,
        "stage1_attempts": outcome.stage1_attempts,
    }
```

- [ ] **Step 4: Point the CLI at the shared serializer**

In `src/nfl_chatdb/cli.py`: delete the local `_outcome_to_dict` function; add `from nfl_chatdb.formatting import format_result_sample, outcome_to_dict`; in `main()` replace `_outcome_to_dict(outcome)` with `outcome_to_dict(outcome)`. If `tests/test_cli.py` imports or references `_outcome_to_dict`, update it to `outcome_to_dict`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (46 + 2 new = 48 passed, 4 deselected).

- [ ] **Step 6: Commit**

```bash
git add src/nfl_chatdb/formatting.py src/nfl_chatdb/cli.py tests/test_formatting.py tests/test_cli.py
git commit -m "refactor: share outcome_to_dict serializer, surface truncated flag"
```

---

### Task 2: `Api.ask` bridge method

**Files:**
- Create: `src/nfl_chatdb/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `formatting.outcome_to_dict` (Task 1); `pipeline.answer_question`; `database.connect`, `database.QueryError`; `stage1_sql.Stage1Error`; `anthropic.APIError`.
- Produces:
  - `app.Api(client, schema_text: str, db_path)` — constructor stores all three.
  - `Api.ask(question: str) -> dict` — on success returns `outcome_to_dict(...)`; on a blank question or any of `Stage1Error` / `anthropic.APIError` / `QueryError` returns `{"error": "<message>"}`. Never raises those. Opens and closes a fresh connection per call.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app.py`. Reuse `conftest.py` fixtures — inspect `tests/conftest.py` for the fake-client and temp-DB fixture names (`test_pipeline.py` / `test_cli.py` show the usage pattern) and mirror them.

```python
import anthropic
import pytest

from nfl_chatdb.app import Api
from nfl_chatdb.stage1_sql import Stage1Error


# Adjust fixture names to match conftest.py.
def _api(client, tmp_db, schema_text):
    return Api(client, schema_text, tmp_db)


def test_ask_happy_path(fake_client_valid, tmp_db_path, schema_text):
    api = _api(fake_client_valid, tmp_db_path, schema_text)
    d = api.ask("How many teams are there?")
    assert "error" not in d
    assert set(d) >= {
        "sql", "columns", "rows", "row_count",
        "caveated", "stage1_attempts", "truncated",
    }


def test_ask_blank_question_returns_error(fake_client_valid, tmp_db_path, schema_text):
    api = _api(fake_client_valid, tmp_db_path, schema_text)
    d = api.ask("   ")
    assert d["error"]


def test_ask_wraps_stage1_error(tmp_db_path, schema_text):
    class Boom:
        def __getattr__(self, _):
            raise Stage1Error(last_error="no working query")
    # If Stage1Error signature differs, construct it as the source defines.
    api = Api(Boom(), schema_text, tmp_db_path)
    d = api.ask("anything")
    assert "error" in d and "no working query" in d["error"]


def test_ask_wraps_api_error(tmp_db_path, schema_text, monkeypatch):
    import nfl_chatdb.app as appmod

    def boom(*a, **k):
        raise anthropic.APIError("rate limited", request=None, body=None)

    monkeypatch.setattr(appmod, "answer_question", boom)
    api = Api(object(), schema_text, tmp_db_path)
    d = api.ask("anything")
    assert "error" in d
```

Verify the real `Stage1Error.__init__` signature and `anthropic.APIError` constructor args; adjust the test if they differ. If there is no existing temp-DB fixture, build one from `conftest.py`'s schema-creation helper or `ingest`'s table DDL — a DB with the tables the pipeline's smoke questions need.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nfl_chatdb.app'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/nfl_chatdb/app.py`:

```python
"""pywebview desktop app for the NFL ChatDB pipeline."""

from __future__ import annotations

from pathlib import Path

import anthropic

from nfl_chatdb.database import DEFAULT_DB_PATH, QueryError, connect
from nfl_chatdb.formatting import outcome_to_dict
from nfl_chatdb.pipeline import answer_question
from nfl_chatdb.stage1_sql import Stage1Error

_INDEX_HTML = Path(__file__).parent / "web" / "index.html"


class Api:
    """Exposed to the page via pywebview's js_api bridge."""

    def __init__(self, client, schema_text: str, db_path=DEFAULT_DB_PATH):
        self._client = client
        self._schema_text = schema_text
        self._db_path = db_path

    def ask(self, question: str) -> dict:
        question = (question or "").strip()
        if not question:
            return {"error": "Enter a question."}
        try:
            conn = connect(self._db_path)
        except QueryError as err:
            return {"error": str(err)}
        try:
            outcome = answer_question(
                self._client,
                question,
                conn=conn,
                schema_text=self._schema_text,
            )
            return outcome_to_dict(outcome)
        except Stage1Error as err:
            return {"error": f"Could not produce a working query: {err.last_error}"}
        except anthropic.APIError as err:
            return {"error": f"Anthropic API call failed: {err}"}
        except QueryError as err:
            return {"error": str(err)}
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nfl_chatdb/app.py tests/test_app.py
git commit -m "feat: Api.ask bridge for the desktop app"
```

---

### Task 3: `main()` startup guard + window launch

**Files:**
- Modify: `src/nfl_chatdb/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `schema.load_schema_text`, `cli.build_client`, `database.connect`, plus `Api` (Task 2).
- Produces: `app.main(argv=None) -> int`. Returns non-zero and prints guidance (no window) when the API key or `data/nfl.db` is missing. On success creates the pywebview window and calls `webview.start()`, returns 0. `webview` is imported lazily inside `main` so tests need not install/patch it for the failure paths.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_main_missing_db_exits_nonzero(tmp_path, monkeypatch, capsys, schema_text):
    import nfl_chatdb.app as appmod

    monkeypatch.setattr(appmod, "load_schema_text", lambda: schema_text)
    monkeypatch.setattr(appmod, "build_client", lambda: object())
    monkeypatch.setattr(appmod, "DEFAULT_DB_PATH", tmp_path / "nope.db")

    rc = appmod.main([])
    assert rc != 0
    assert "ingest" in capsys.readouterr().out


def test_main_missing_key_exits_nonzero(monkeypatch, capsys, schema_text):
    import nfl_chatdb.app as appmod

    monkeypatch.setattr(appmod, "load_schema_text", lambda: schema_text)

    def no_key():
        raise anthropic.AnthropicError("no api key")

    monkeypatch.setattr(appmod, "build_client", no_key)
    rc = appmod.main([])
    assert rc != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -q`
Expected: FAIL — `AttributeError: module 'nfl_chatdb.app' has no attribute 'main'`.

- [ ] **Step 3: Write minimal implementation**

Add imports to `src/nfl_chatdb/app.py`:

```python
import argparse

from nfl_chatdb.cli import build_client
from nfl_chatdb.schema import load_schema_text
```

Add:

```python
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Desktop app for asking NFL-stats questions."
    )
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH

    try:
        schema_text = load_schema_text()
    except FileNotFoundError as err:
        print(f"{err}\nRun `uv run python -m nfl_chatdb.ingest` first.")
        return 2

    try:
        client = build_client()
    except anthropic.AnthropicError as err:
        print(f"Anthropic client could not be created: {err}\n"
              "Set ANTHROPIC_API_KEY (e.g. in a .env file).")
        return 2

    try:
        connect(db_path).close()
    except QueryError as err:
        print(f"{err}\nRun `uv run python -m nfl_chatdb.ingest` first.")
        return 2

    import webview

    api = Api(client, schema_text, db_path)
    webview.create_window(
        "NFL ChatDB",
        url=str(_INDEX_HTML),
        js_api=api,
        width=900,
        height=700,
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: `main` reads `DEFAULT_DB_PATH` at call time (module attribute) so the test's `monkeypatch.setattr(appmod, "DEFAULT_DB_PATH", ...)` takes effect — reference it as a module global, do not bind it as a default arg.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all unit tests, 4 deselected).

- [ ] **Step 6: Commit**

```bash
git add src/nfl_chatdb/app.py tests/test_app.py
git commit -m "feat: desktop app startup guard and window launch"
```

---

### Task 4: Frontend page, packaging, launch verification

**Files:**
- Create: `src/nfl_chatdb/web/index.html`
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `window.pywebview.api.ask(question)` → Promise resolving to the `outcome_to_dict` shape or `{error}`.
- Produces: the running `uv run nfl-chatdb-app` command.

- [ ] **Step 1: Add the dependency and script**

In `pyproject.toml`:
- Add `"pywebview"` to `[project].dependencies`.
- Under `[project.scripts]` add: `nfl-chatdb-app = "nfl_chatdb.app:main"`.
- Ensure non-`.py` package data ships: add
  ```toml
  [tool.hatch.build.targets.wheel.force-include]
  "src/nfl_chatdb/web" = "nfl_chatdb/web"
  ```
  (or confirm hatch already includes it — `src/nfl_chatdb/web/` is under the package dir, so it typically does; the explicit entry is safe).

Run: `uv sync`
Expected: `pywebview` installed, lockfile updated.

- [ ] **Step 2: Create `src/nfl_chatdb/web/index.html`**

One file, inline `<style>` + `<script>`, no external assets. Requirements:
- `<input id="q">` + `<button id="ask">Ask</button>`. Enter in the input triggers ask. Button `disabled` when `q.value.trim()` is empty.
- On ask: disable input+button, show a "Thinking…" indicator, `await window.pywebview.api.ask(q.value)`, then render.
- Render (clear the answer container first):
  - If `resp.error`: red banner `<div class="err">` with the text; leave `q.value` as-is; re-enable.
  - Else if `resp.row_count === 0`: `<p>No rows matched.</p>`.
  - Else: build a `<table>` — `resp.columns` as `<th>`, `resp.rows` (array of arrays) as `<tr><td>`. Render `null`/`undefined` as an empty cell.
  - If `resp.truncated`: `<p class="note">Showing the first 10,000 rows.</p>`.
  - If `resp.caveated`: amber `<div class="warn">⚠ This answer may not fully match the question.</div>` followed by a `<ul>` of `resp.issues`.
  - Always (on non-error): a `<details><summary>How this was answered</summary>` containing `<pre>` of `resp.sql`, `Stage 1 attempts: {stage1_attempts}`, `Stage 2 verdict: {caveated ? "caveated" : "valid"}`, and a `<ul>` of `issues` when non-empty.
- Guard against `window.pywebview` being undefined at load (pywebview injects it slightly after `DOMContentLoaded`): listen for the `pywebviewready` event, or feature-check inside the click handler.
- Keep styling minimal: a readable system font stack, max-width container, light background, table borders. No external fonts or CDNs.

- [ ] **Step 3: Manual launch verification**

Run: `uv run nfl-chatdb-app`
Expected: a native window titled "NFL ChatDB" opens. Ask *"How many rushing touchdowns did Derrick Henry score in 2023?"* → a table with `12` appears within ~30s; "How this was answered" expands to show the SQL. Close the window; process exits 0.

If the window is blank: check the `url=` path resolves (print `_INDEX_HTML`), and that `web/index.html` was created under `src/nfl_chatdb/`.

- [ ] **Step 4: Update README**

Add a "Desktop app" section after "Ask a question":

```markdown
## Desktop app

```bash
uv run nfl-chatdb-app
```

A native window with a single question box and an expandable
"how this was answered" panel (generated SQL, Stage 1 retries,
Stage 2 verdict).
```

- [ ] **Step 5: Run the full suite once more**

Run: `uv run pytest -q`
Expected: PASS (4 deselected).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/nfl_chatdb/web/index.html README.md
git commit -m "feat: desktop app frontend page and packaging"
```

---

## Self-Review Notes

- **Spec coverage:** serializer refactor (T1), `Api.ask` + all error rows (T2), startup guards + window (T3), frontend rendering rules + packaging + launch (T4). All spec sections mapped.
- **Types:** `Api(client, schema_text, db_path)` and `Api.ask(question) -> dict` consistent across T2/T3. `outcome_to_dict` key list consistent between spec, T1, and the frontend contract in T4.
- **Known verification points for the executor:** exact constructor signatures of `Stage2Verdict`, `Stage1Error`, and `anthropic.APIError`, and the `conftest.py` fixture names — each flagged inline in the task where it matters.
