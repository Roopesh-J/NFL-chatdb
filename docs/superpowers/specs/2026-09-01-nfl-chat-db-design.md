# NFL Chat-With-Your-Database — Design

## Overview

A natural-language-to-SQL portfolio project over NFL statistics. The
thesis: most "chat with your database" demos stop at "the SQL executed
without error," which is a weak bar — a query can run cleanly and
still answer the wrong question. This project demonstrates a two-stage
pipeline that separates "can I write valid SQL" from "does this SQL
actually mean what was asked," and does so **without** a hand-written
documentation/semantic layer on the database.

NFL is the domain specifically because its vocabulary (quarter,
completion, red zone, EPA, air yards) is already understood by an
LLM's general knowledge. That substitutes for the missing semantic
layer in a way a business-specific schema could not — an LLM has no
built-in intuition for what `flag_3` or `mrr_adj` means in an arbitrary
company's warehouse, but it does know what a rushing touchdown is.
This is a deliberate, fixed design constraint, not a placeholder for a
future glossary: adding a real semantic layer on top of NFL data later
would undermine the demo, since the LLM could partly succeed without
it, making it impossible to cleanly show the glossary is what's doing
the work. A semantic-layer demo belongs on a domain the LLM has no
built-in intuition for (see Future Ideas).

## Data Layer

Six nflverse datasets, ingested via `nfl_data_py` into a local SQLite
file, spanning four grains so Stage 1 can pick the coarsest table that
answers the question:

- **`play_by_play`** — per-play event log, most granular table,
  self-contained (player names, teams, down/distance, EPA, air yards,
  TD flags all inline as columns — no joins needed to make sense of a
  row). ~200k rows × ~400 columns; a full scan is ~6s, so it is the
  slow path and the other tables are preferred when they suffice.
- **`seasonal_stats`** — pre-aggregated per-player, per-season totals.
- **`weekly`** — per-player, per-game stats (`import_weekly_data`).
  Added 2026-09-09: originally cut on the theory that a single week is
  cheap to aggregate from `play_by_play`, but questions that aggregate
  *across* games ("best QB record in one-score games") then have to
  reconstruct per-game outcomes from ~200k play rows — the 75s case
  that motivated adding it. ~28k rows.
- **`schedules`** — one row per game (`import_schedules`): final
  scores, `result` (home margin), spreads, moneylines, over/under,
  weather, roof/surface, coaches, starting QBs. Added 2026-09-09. The
  canonical source for game outcomes; ~1.1k rows.
- **`rosters`** — per-player, per-season bio (`import_seasonal_rosters`,
  already pulled for the `seasonal_stats` name merge, now also stored):
  position, height, weight, college, age, `years_exp`, `entry_year` /
  `rookie_year`, draft club/number. Added 2026-09-09 — without it the
  pipeline could answer no "who" attribute question. Third-party id
  crosswalk columns and `headshot_url` are dropped at ingest
  (`_DROP_COLUMNS`) to keep the schema snapshot lean.
- **`snap_counts`** — per-player, per-game snap participation
  (offense/defense/special-teams counts and percentages), sourced from
  PFR. Genuinely independent data — pbp only captures plays a player
  was directly involved in, not every snap on the field.

**Join keys:** nflverse `game_id` (`2023_01_KC_DET` form) and GSIS
`player_id` (`00-0035700` form) are consistent across `play_by_play`,
`weekly`, `seasonal_stats`, `schedules`, and `rosters`.

**Known wrinkle (not blocking MVP):** `snap_counts` uses PFR's own
`pfr_game_id` / `pfr_player_id` scheme, which doesn't cleanly join
against the nflverse keys. Any cross-table question involving snaps
(e.g., "yards per snap") needs an ID-mapping step at ingestion time.

Explicitly excluded: schedules, rosters, officials, trades, draft
picks, combine results, depth charts, injury reports, ESPN QBR,
contracts — none of these are game stats, which is the deliberate
focus.

Ingestion is a one-time (or periodically re-run) script: pull each
dataset as a DataFrame via `nfl_data_py`, write to SQLite via
`to_sql()`. No ORM, no migrations — this is a static, read-only
analytical dataset, not an evolving application database.

## Interface

CLI/script/notebook only for MVP. Interface design is explicitly a
non-issue — the project's value is in pipeline functionality, not
end-user access.

## Scope: Single-Turn Only

Each question is independent; there is no conversation memory across
questions. Multi-turn support (resolving references like "what about
receiving TDs?" against a prior question's context) is deferred — it
would require carrying conversation history into Stage 1's context,
which is a meaningfully larger scope than stateless single-shot
queries.

## Pipeline Architecture

Two stages, both built on the raw Anthropic API (no agent framework
for MVP — see Tech Stack below).

### Stage 1: SQL Generation

A hand-rolled loop using a cheaper/faster model, since this stage's
job is mechanically simpler than Stage 2's judgment call:

1. System prompt includes the **static schema** (table names, columns,
   dtypes) baked in directly. The schema never changes, so there's no
   need for a `get_schema()` tool call to discover it on every query —
   this removes a guaranteed round-trip per request.
2. Model generates a SQL query from the user's question.
3. The query executes directly against SQLite (read-only `SELECT`s
   only — no dry-run distinction needed, since there's no write risk
   to guard against).
4. If execution **errors** (bad column name, syntax error) or returns
   something degenerate (e.g., an empty result set), the error is fed
   back to the model for one corrective retry. This execution-retry
   loop is capped at **2 attempts total** (initial + 1 retry).
5. Once a query executes successfully, Stage 1 hands off the final SQL
   text plus the result set (row count + a small sample) to Stage 2.

### Stage 2: Semantic Validation

A single-shot call using a stronger model, with forced structured
output (no free-text critique):

- **Inputs:** the original NL question, the final SQL from Stage 1,
  the static schema, and the result sample (row count + first ~5-10
  rows).
- **Output:** `{valid: bool, issues: [...], suggested_fix: string |
  null}` — a specific diagnosis, not just pass/fail.
- Including the result sample (not just SQL text) lets Stage 2 catch a
  distinct error class that pure SQL/schema review misses: an empty
  result set, one row where the question implies a ranked list, a
  percentage over 100, a negative count. It costs nothing extra, since
  Stage 1 already executed the query.

### End-to-End Flow

1. User asks a question.
2. Stage 1 generates SQL, executes it, self-corrects on execution
   errors (up to 2 attempts).
3. Stage 2 validates the successful query against the original
   question.
4. **If valid** → return the result to the user.
5. **If invalid** → Stage 2's diagnosis is fed back to Stage 1 as a
   targeted correction instruction (not a blind "try again"). Stage 1
   regenerates and re-executes (its own 2-attempt execution loop
   applies again). Stage 2 validates once more. This is the single
   semantic retry — capped at 1, not "up to N attempts," to bound cost
   and latency.
6. **If still invalid after that retry** → **annotate, don't block.**
   Return the answer along with Stage 2's caveat (e.g., "note: this
   query only counts rushing TDs, not all TD types") rather than
   refusing outright. A flagged, caveated answer is more useful — and
   a better demo of the thesis — than silence.

## Tech Stack

**Raw Anthropic API for both stages, hand-rolled loop for Stage 1.**
Considered three options:

- **Agent SDK for Stage 1 / raw API for Stage 2** — technically
  reasonable (different mechanisms for different jobs), but Stage 1's
  loop is now thin (schema pre-loaded, retry capped at 1) — a full
  agent framework is more machinery than the remaining 1-2 tool turns
  need.
- **Agent SDK for both stages** — ruled out. Stage 2 doesn't loop or
  use tools; wrapping a single reasoning pass in an agent framework
  misrepresents what the code is doing.
- **Raw API for both, hand-rolled loop for Stage 1 (chosen)** — one
  dependency, one mental model, and the loop is small enough
  (well under 100 lines) that hand-rolling it is more transparent than
  going through SDK abstractions. Stage 2 as a pure function
  (prompt+SQL+schema+result → verdict) is also easiest to unit test
  this way.

Open door: revisit Agent SDK for Stage 1 later if its loop grows more
complex (more tools, more turns).

## Cost & Latency Controls

- Schema is pre-loaded into the system prompt, not tool-discovered —
  removes a guaranteed round-trip per query.
- Execution-retry loop (Stage 1) capped at 2 attempts.
- Semantic-mismatch retry (Stage 2 → Stage 1) capped at 1 attempt.
- Cheaper/faster model for Stage 1's exploration; stronger model
  reserved for Stage 2's judgment call, where reasoning quality matters
  most.

This is a portfolio MVP, not a production service under real traffic —
total dollar cost per demo run is small regardless. The caps above are
mainly about bounding **latency** (a user waiting through a chain of
API calls) as much as cost.

## Testing

- **Stage 2 as a pure function** — its biggest testing advantage.
  Since it's a single-shot call with structured output (no tool loop,
  no state), it can be tested in isolation: fixed
  (prompt, SQL, schema, result-sample) fixtures in, verdict out. This
  is the basis for a small regression suite.
- **Ingestion script** — sanity checks that each table loads with
  expected columns/row counts after `to_sql()`.
- **A handful of smoke-test questions** run end-to-end through the
  full pipeline, checking that it doesn't error and returns a sane
  shape. This is *not* the formal adversarial eval set (see below,
  deferred) — just enough for MVP confidence that the pipeline holds
  together.

## Non-Goals

**Fixed by design** (not a timing issue — central to the thesis):

- No documentation/glossary (semantic) layer on the database. NFL's
  domain knowledge is the deliberate substitute; see Overview.

**Deferred for now** (candidates for v2+, not ruled out permanently):

- Multi-turn conversation memory
- Correction-memory / saved-query reuse, metric-registry tiering
- A formal adversarial eval set (prompts paired with a
  plausible-but-wrong SQL trap, to concretely demonstrate Stage 2
  catching what a naive pipeline would miss)
- A real UI beyond CLI/script/notebook
- Additional datasets beyond `play_by_play` / `seasonal_stats` /
  `snap_counts`, if the game-stats focus later expands

## Future Ideas (not part of this project)

A **separate** portfolio piece demonstrating the opposite thesis — a
semantic layer as ground truth for text-to-SQL — on a domain the LLM
has no built-in intuition for (e.g., a synthetic SaaS/e-commerce
schema with deliberately opaque naming: `flag_3`, `cohort_id`,
`mrr_adj`). This would not be a variant of the NFL project; it tests a
different claim and needs a domain where a hand-written glossary is
the only thing standing between the model and a correct query.
