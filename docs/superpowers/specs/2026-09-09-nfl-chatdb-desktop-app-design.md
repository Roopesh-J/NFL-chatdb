# NFL ChatDB — Desktop App Design

**Date:** 2026-09-09
**Status:** Approved (verbally, in brainstorming session)

## Goal

A local desktop application wrapping the existing two-stage NL-to-SQL
pipeline (`nfl_chatdb.pipeline.answer_question`). Nicer to use than the
CLI; showcases the two-stage "is it valid" / "does it mean what was
asked" thesis. Local-only — no deploy, no auth, no server.

## Non-goals (YAGNI)

Conversational follow-ups, query history/persistence, editing/re-running
the generated SQL, CSV export, `.app` bundling, any web deploy. All easy
to add later.

## Stack

`pywebview` — a native OS window rendering one self-contained HTML page.
Python ↔ page communication via pywebview's `js_api` bridge
(`window.pywebview.api.ask(...)`). No HTTP server, no ports, no CORS.

## Components

### `src/nfl_chatdb/app.py`

- **`Api`** — the object exposed to JS via `js_api`. Constructed with
  `client`, `schema_text`, `db_path`.
  - `ask(question: str) -> dict` — runs on pywebview's API thread. Opens
    a fresh read-only connection (`database.connect(db_path)`), calls
    `answer_question(client, question, conn=conn, schema_text=schema_text)`,
    returns `formatting.outcome_to_dict(outcome)`. Connection closed in a
    `finally`. Catches `Stage1Error`, `anthropic.APIError`, `QueryError`
    and returns `{"error": "<message>"}` instead of raising. A blank /
    whitespace-only question returns `{"error": "..."}` too (JS also
    guards).
  - Fresh connection per call avoids sharing a `sqlite3` handle across
    pywebview's UI thread and API thread.
- **`main()`** —
  1. `schema_text = load_schema_text()`
  2. `client = build_client()` (runs `load_dotenv()`; raises
     `anthropic.AnthropicError` if no key)
  3. Verify `db_path` exists (`database.connect` raises `QueryError` if
     not).
  4. On any of the above failing: print the CLI's guidance
     (`"<err>\nRun \`uv run python -m nfl_chatdb.ingest\` first."` for the
     DB case; a clear key message otherwise) and return a non-zero exit
     code — *before* opening a window.
  5. `webview.create_window("NFL ChatDB", url=<index.html path>,
     js_api=Api(...), width=900, height=700)`; `webview.start()`; return 0.
- Module guard: `if __name__ == "__main__": raise SystemExit(main())`.

### `src/nfl_chatdb/web/index.html`

One file, inline `<style>` and `<script>`, no external assets, no build
step.

- **Input row:** text `<input>` + **Ask** button. Enter submits. Both
  disabled while a call is in flight; a spinner/"Thinking…" indicator
  shows. Button disabled when input is blank/whitespace.
- **Answer area** (replaced each ask):
  - Result rendered as an HTML `<table>` (`columns` → `<th>`, `rows` →
    `<td>`). `null` shown as an empty cell.
  - `row_count == 0` → "No rows matched." message instead of a table.
  - `truncated` true → a note: "Showing first 10,000 rows."
  - `caveated` true → an amber banner: "⚠ This answer may not fully
    match the question." followed by the `issues` list.
  - `error` present → a red banner with the message; question text is
    left in the input for retry/edit.
- **`<details>` "How this was answered":**
  - `sql` in a `<pre>`.
  - "Stage 1 attempts: N"
  - "Stage 2 verdict: valid" / "caveated"
  - `issues` as a list when present.
- JS is deliberately logic-light: build a table, toggle banners, call the
  bridge, render. No framework.

### `src/nfl_chatdb/formatting.py` (refactor)

Move `_outcome_to_dict` from `cli.py` to here as public
**`outcome_to_dict(outcome: PipelineOutcome) -> dict`**. Add the
`truncated` field (`outcome.result.truncated`), currently dropped.
`cli.py` imports and uses it. Both CLI `--json` and the app share one
serializer.

## Data flow (one question)

1. User types, submits → JS disables input, shows spinner, calls
   `window.pywebview.api.ask(q)` → promise.
2. `Api.ask` (API thread): `connect(db_path)` →
   `answer_question(...)` (blocking, ~5–30 s) → `outcome_to_dict` →
   returns dict; connection closed in `finally`.
3. JS receives dict: renders table + banners + details, re-enables input.

## Error handling

| Situation | Behaviour |
|---|---|
| No `ANTHROPIC_API_KEY` at startup | Print key guidance, non-zero exit, no window |
| `data/nfl.db` missing at startup | Print `"<err>\nRun \`uv run python -m nfl_chatdb.ingest\` first."`, non-zero exit, no window |
| `Stage1Error` during `ask` | `{"error": "Could not produce a working query: <last_error>"}` |
| `anthropic.APIError` during `ask` | `{"error": "Anthropic API call failed: <err>"}` |
| `QueryError` during `ask` | `{"error": "<str>"}` |
| Blank question | `{"error": "Enter a question."}`; JS also keeps button disabled |

## Testing

`tests/test_app.py` — pytest, reuses `conftest.py` fixtures (fake client,
temp SQLite DB). Does **not** import `webview`.

- `Api.ask` happy path: fake client + temp DB → asserts dict shape
  (`sql`, `columns`, `rows`, `row_count`, `caveated`, `stage1_attempts`,
  `truncated`).
- `Api.ask` with a client that raises `Stage1Error` → returns
  `{"error": ...}`, does not raise.
- `Api.ask` with a client that raises `anthropic.APIError` → returns
  `{"error": ...}`.
- `Api.ask` with a blank question → `{"error": ...}`, no client call.
- `main()` startup guard: point at a nonexistent DB path → non-zero
  return / `SystemExit`, no `webview` call (patch `webview` or structure
  `main` so the guard runs first).

`tests/test_formatting.py` — extend for `outcome_to_dict` including the
`truncated` field. Update `tests/test_cli.py` if it referenced
`_outcome_to_dict`.

Frontend JS: no JS test infra in this project and none added. JS kept
logic-light; manual check on first run.

## `pyproject.toml`

- Add dependency `pywebview`.
- Add script: `nfl-chatdb-app = "nfl_chatdb.app:main"`.
- `index.html` ships inside the package (`src/nfl_chatdb/web/`); ensure
  hatch includes it (it's under the package dir, so the existing
  `packages = ["src/nfl_chatdb"]` covers it — verify non-`.py` files are
  included, add `[tool.hatch.build]` `artifacts`/`force-include` if not).

## Launch

```bash
uv sync
uv run nfl-chatdb-app
```
