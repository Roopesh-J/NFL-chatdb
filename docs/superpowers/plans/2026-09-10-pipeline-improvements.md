# Pipeline Improvements &mdash; Implementation Plan

**Date:** 2026-09-10
**Branch:** `pipeline-improvements`
**Context:** batched improvements agreed in the 2026-09-10 design conversation.

## Order of work (one commit per item)

1. **B2 &mdash; trim the schema snapshot.** `render_schema_snapshot` in `ingest.py`
   keeps an allowlist of `play_by_play` columns (~60 of ~400) instead of all;
   other five tables kept in full. Regenerate `schema_snapshot.txt`.
   *Why first:* every later prompt embeds this text; smaller is cheaper and a
   smaller cached prefix.

2. **C3 &mdash; `_FENCE_RE` first-token bug.** A `` ```SELECT `` fence with no
   newline currently drops `SELECT`. Fix the regex; add a test.

3. **A2 &mdash; gate the semantic retry.** Add `retry_worthwhile: bool = True` to
   `Stage2Verdict`; Stage 2's prompt sets it `false` when the problem is inherent
   ambiguity a rewrite can't fix. Pipeline loop:
   `while not verdict.valid and verdict.retry_worthwhile and retries < max`.

4. **A1 &mdash; Stage 3 answer synthesis.** New `stage3_answer.py`:
   `synthesize_answer(client, question, sql, result_sample, verdict) -> AnswerSummary`
   (`{ answer: str, reliable: bool }`), model **Haiku 4.5**, no thinking.
   - Pipeline: after the settled result (post-retry, post-fallback), run Stage 3.
     The second Stage 2 inside the retry loop is **removed** &mdash; the loop now
     does `S1 retry` only, then falls through to Stage 3.
   - `PipelineOutcome` gains `answer: str` and `reliable: bool`; `caveated`
     becomes `not reliable` (Stage 3's judgment, informed by the last verdict).
   - `outcome_to_dict` carries `answer` + `reliable`.
   - UI: the answer sentence is the headline above the hero/table; the caveat
     banner keys off `reliable`.
   - Fold **A3 (prose assumptions)** in: Stage 3's prompt asks it to state the
     assumptions it sees baked into the SQL ("ranked by win %, min N games,
     regular + playoffs") as part of the answer when they matter.

5. **B1 &mdash; schema prompt caching.** One shared system preamble
   (`common instructions + schema`, `cache_control: {type: "ephemeral", ttl: "1h"}`)
   used by every call; stage-specific instructions move into the user message
   after the breakpoint. Schema moves out of the user message. Verify with
   `response.usage.cache_read_input_tokens` in a live test.

6. **C1 &mdash; `cli.py` missing-key error.** Widen `except anthropic.APIError`
   to also catch `anthropic.AnthropicError` (missing key) and print guidance
   instead of a traceback. Test.

7. **C2 &mdash; CLI shows the two-stage story.** Default (non-`--json`) output
   prints Stage 1 attempts, whether the semantic retry fired, and the Stage 3
   answer line. Test.

8. **D1 + E1 &mdash; README rewrite.** Thesis, the six-table list, an example
   Q&A that shows a caveat, app screenshots (reuse existing), 6 example
   questions, and an explicit note that `snap_counts` can't be joined
   (PFR-ID mismatch) &mdash; scoped out, not fixed.

## Global constraints

- Python `>=3.11,<3.12`; `uv run` for everything.
- Tests: pytest, `tests/`, reuse `conftest.py` fixtures, no `webview` import,
  live tests behind `@pytest.mark.live`.
- Models: Stage 1 first `claude-haiku-4-5`; Stage 1 retry + Stage 2 + fallback
  `claude-sonnet-5`; Stage 3 `claude-haiku-4-5`.
- Every commit: full unit suite green before moving on.

## Verification at the end

- Full unit suite + live suite green.
- Live: the `<=15 total points` question &mdash; Stage 3 says "no meaningful
  ranking", not a 1-game record.
- Live: a clean question &mdash; `cache_read_input_tokens > 0` on the 2nd call.
- Browser: the new answer-sentence headline renders; caveat keys off `reliable`.
