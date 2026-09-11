# NFL Chat-With-Your-Database — Design

## Thesis

A natural-language-to-SQL portfolio project over NFL statistics. Most
"chat with your database" demos stop at *"the SQL executed without
error"* — a weak bar, since a query can run cleanly and still answer
the wrong question. This project separates *"can I write valid SQL"*
from *"does this SQL actually mean what was asked"*, and does so
**without** a hand-written semantic/glossary layer on the database.

NFL is the domain specifically because its vocabulary (quarter,
completion, red zone, EPA, air yards) is already understood by an LLM's
general knowledge. That substitutes for the missing semantic layer in a
way a business schema could not — an LLM has no built-in intuition for
what `flag_3` or `mrr_adj` means in an arbitrary warehouse, but it does
know what a rushing touchdown is.

This is a deliberate, fixed constraint, not a placeholder for a future
glossary. Adding a real semantic layer on top of NFL data later would
undermine the demo: the LLM could partly succeed without it, making it
impossible to show cleanly that the glossary is what's doing the work.
A semantic-layer demo belongs on a domain the LLM has no intuition for
(see Future Ideas).

## Data layer

Six nflverse datasets, ingested via `nfl_data_py` into a local SQLite
file, spanning four grains so Stage 1 can pick the coarsest table that
answers the question:

| Table | Grain | Notes |
|---|---|---|
| `schedules` | one row per game | scores, `result` (home margin), spreads, moneylines, over/under, weather, roof/surface, coaches, starting QBs — the canonical source for game outcomes |
| `seasonal_stats` | one row per player per season | pre-aggregated totals |
| `weekly` | one row per player per game | per-game stats; lets cross-game questions ("best QB record in one-score games") avoid reconstructing outcomes from ~200k play rows |
| `rosters` | one row per player per season | position, height, weight, college, age, `years_exp`, draft; the only source for "who" attribute questions. Third-party id crosswalk columns and `headshot_url` are dropped at ingest |
| `snap_counts` | one row per player per game | offense/defense/special-teams snap participation, sourced from PFR. Genuinely independent — pbp only captures plays a player was directly involved in |
| `play_by_play` | one row per play | per-play event log, self-contained (names, teams, down/distance, EPA, air yards, TD flags all inline). ~200k rows × ~400 columns; a full scan is ~6s, so it is the slow path and other tables are preferred when they suffice |

**Join keys:** nflverse `game_id` (`2023_01_KC_DET`) and GSIS
`player_id` (`00-0035700`) are consistent across `play_by_play`,
`weekly`, `seasonal_stats`, `schedules`, and `rosters`.

**Known wrinkle:** `snap_counts` uses PFR's own `pfr_game_id` /
`pfr_player_id` scheme, which doesn't cleanly join against the nflverse
keys. Any cross-table question involving snaps ("yards per snap") needs
an id-mapping step at ingestion — scoped out, not fixed.

Explicitly excluded: officials, trades, draft picks, combine results,
depth charts, injury reports, ESPN QBR, contracts — none are game
stats, which is the deliberate focus.

Ingestion is a one-time (or periodically re-run) script: pull each
dataset as a DataFrame, write to SQLite via `to_sql()`. No ORM, no
migrations — a static, read-only analytical dataset, not an evolving
application database. The script also regenerates `schema_snapshot.txt`
(see Prompt caching).

## Interface

Two entry points over the same `pipeline.answer_question`:

- **CLI** (`nfl-chatdb "<question>"`) — prints the pipeline story
  (Stage 1 attempts, whether the semantic retry fired, the Stage 3
  answer line); `--json` emits the serialized outcome.
- **Desktop app** (`nfl-chatdb-app`) — `pywebview` opens a native
  window rendering one self-contained HTML page. Python ↔ page via
  pywebview's `js_api` bridge; no HTTP server, no ports, no CORS.
  Local-only — no deploy, no auth. A single question box plus an
  expandable "how this was answered" panel.

Both share one serializer, `formatting.outcome_to_dict`.

## Scope: single-turn only

Each question is independent; no conversation memory across questions.
Multi-turn support (resolving "what about receiving TDs?" against a
prior question) is deferred — it would mean carrying history into
Stage 1's context, a meaningfully larger scope.

## Pipeline architecture

Three stages on the raw Anthropic API (no agent framework — see Tech
stack).

### Stage 1 — SQL generation

Hand-rolled loop on a cheaper/faster model (Haiku 4.5):

1. The static schema snapshot rides a cached system block (see Prompt
   caching) — no `get_schema()` round-trip per query.
2. The model writes one `SELECT`.
3. It executes directly against SQLite (read-only `SELECT`s only).
4. On an execution error (bad column, syntax) or a degenerate result,
   the error is fed back for one corrective retry. Capped at **2
   attempts total**.
5. The final SQL plus its result (row count + small sample) hands off
   to Stage 2.

### Stage 2 — semantic validation

A single-shot call on a stronger model (Sonnet 5), forced structured
output:

- **Inputs:** the original question, the final SQL, the schema, the
  result sample.
- **Output:** `{valid, issues[], retry_worthwhile}` — a specific
  diagnosis, plus whether a rewrite could plausibly help.
- Passing the result sample (not just SQL text) catches an error class
  pure SQL review misses: an empty set, one row where a ranked list
  was implied, a percentage over 100, a negative count. It costs
  nothing extra — Stage 1 already executed the query.

### Retry and fallback

- **Semantic retry** — if Stage 2 says invalid *and* `retry_worthwhile`
  *and* a retry is left: the full issue list plus the rejected SQL go
  back to Stage 1, which regenerates on the stronger model. Capped at
  one. There is **no second Stage 2 pass** — a re-check verdict can't
  drive anything once the budget is spent.
- **Zero-row fallback** — if the query returned no rows and Stage 2
  rejected it: ask for a raw "list the matching records, no
  aggregation, LIMIT 50" query so an over-filtered ranking shows the
  underlying data instead of nothing.

### Stage 3 — answer synthesis

Runs once on the settled result, on Haiku 4.5. Writes a one-to-three
sentence plain-English answer that names any debatable modelling choice
("best" = win %, a minimum-games threshold), and sets `reliable=false`
when the result doesn't really answer the question — empty, a one-row
ranking on a tiny sample, implausible numbers, an unanswerably vague
question. The CLI and app lead with this sentence; `reliable` drives
the caveat styling.

### Prompt caching

The ~6k-token schema snapshot rides a byte-identical, one-hour-cached
`system` block shared by Stage 1, Stage 2, and the fallback (see
`prompts.cached_schema_system`). Written to cache once per model per
hour, read back at ~10% price on every later call.

## Tech stack

**Raw Anthropic API for every stage; hand-rolled loop for Stage 1.**
Stage 1's loop is thin (schema pre-loaded, retry capped at 1) — a full
agent framework is more machinery than 1–2 tool turns need. Stage 2 and
Stage 3 don't loop or use tools; wrapping a single reasoning pass in an
agent framework would misrepresent the code. One dependency, one mental
model, and Stage 2/3 as pure functions (inputs → verdict/summary) are
easiest to unit-test.

Open door: revisit the Agent SDK for Stage 1 if its loop grows (more
tools, more turns).

## Cost & latency controls

- Schema pre-loaded into a cached system block, not tool-discovered.
- Only the curated ~95-column subset of `play_by_play` goes in the
  snapshot (the DB keeps all ~400).
- Execution-retry loop capped at 2 attempts; semantic retry capped at
  1 and skipped when Stage 2 says a rewrite won't help.
- Haiku for Stage 1's first pass and Stage 3; Sonnet for Stage 2 and
  the Stage 1 retry.

Total dollar cost per demo run is small regardless; the caps mainly
bound **latency** — a user waiting through a chain of API calls.

## Testing

- **Stage 2 and Stage 3 as pure functions** — single-shot calls with
  structured output, tested in isolation with fixed fixtures.
- **Ingestion** — sanity checks that each table loads with expected
  columns/row counts; `render_schema_snapshot` trimming behaviour.
- **Smoke questions** run end-to-end (behind `-m live`) — enough
  confidence that the pipeline holds together. Not a formal
  adversarial eval set (deferred).

## Non-goals

**Fixed by design** (central to the thesis):

- No documentation/glossary (semantic) layer on the database — NFL
  domain knowledge is the deliberate substitute.

**Deferred** (candidates for later, not ruled out):

- Multi-turn conversation memory.
- Correction-memory / saved-query reuse, metric-registry tiering.
- A formal adversarial eval set — prompts paired with a
  plausible-but-wrong SQL trap, to concretely demonstrate Stage 2
  catching what a naive pipeline would miss.
- An `snap_counts` id crosswalk to unlock per-snap cross-table
  questions.

## Future ideas (not part of this project)

A **separate** portfolio piece demonstrating the opposite thesis — a
semantic layer as ground truth for text-to-SQL — on a domain the LLM
has no built-in intuition for (a synthetic SaaS/e-commerce schema with
deliberately opaque naming: `flag_3`, `cohort_id`, `mrr_adj`). It tests
a different claim and needs a domain where a hand-written glossary is
the only thing between the model and a correct query.
