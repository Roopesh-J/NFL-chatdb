# tests/test_pipeline.py

from nfl_chatdb.database import connect
from nfl_chatdb.pipeline import PipelineOutcome, answer_question
from nfl_chatdb.stage2_validate import Stage2Verdict
from nfl_chatdb.stage3_answer import AnswerSummary


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 100
    output_tokens = 20
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _CreateResponse:
    def __init__(self, text):
        self.content = [_Block(text)]
        self.stop_reason = "end_turn"
        self.usage = _Usage()


class _ParseResponse:
    def __init__(self, verdict):
        self.parsed_output = verdict
        self.usage = _Usage()


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
                fmt = kwargs.get("output_format")
                # Stage 3's call: only consume a scripted AnswerSummary if the
                # test provided one; otherwise return a benign default so
                # tests that don't care about phrasing don't have to script it.
                if fmt is AnswerSummary and not (
                    client._verdicts and isinstance(client._verdicts[0], AnswerSummary)
                ):
                    return _ParseResponse(
                        AnswerSummary(answer="Answer text.", reliable=True)
                    )
                return _ParseResponse(client._verdicts.pop(0))

        self.messages = _M()


SQL_OK = "```sql\nSELECT SUM(rush_touchdown) AS tds FROM play_by_play WHERE season = 2023\n```"
SQL_ALL_TD = "```sql\nSELECT COUNT(*) AS tds FROM play_by_play WHERE season = 2023\n```"
SQL_EMPTY = "```sql\nSELECT rusher_player_name FROM play_by_play WHERE season = 1999 GROUP BY rusher_player_name HAVING COUNT(*) > 5\n```"
SQL_RAW_ROWS = "```sql\nSELECT rusher_player_name, rush_touchdown FROM play_by_play WHERE season = 2023 LIMIT 50\n```"
SQL_STILL_EMPTY = (
    "```sql\nSELECT rusher_player_name FROM play_by_play WHERE season = 1999\n```"
)


def test_valid_first_pass(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_OK],
        verdicts=[Stage2Verdict(valid=True)],
    )
    out = answer_question(
        client,
        "How many rushing TDs in 2023?",
        conn=conn,
        schema_text=fake_schema_text,
    )
    assert isinstance(out, PipelineOutcome)
    assert out.reliable is True
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
        client,
        "rushing TDs in 2023",
        conn=conn,
        schema_text=fake_schema_text,
    )
    assert out.semantic_retries == 1
    assert out.reliable is True
    assert out.result.rows == [(2,)]
    # the correction reached Stage 1's second prompt
    second = str(client.create_calls[1]["messages"])
    assert "rush_touchdown" in second


def test_retry_prompt_carries_all_issues_and_previous_sql(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_ALL_TD, SQL_OK],
        verdicts=[
            Stage2Verdict(
                valid=False,
                issues=[
                    "Returns an id, not a readable name.",
                    "No minimum-games threshold.",
                ],
                suggested_fix="Group by the name column and add HAVING.",
            ),
            Stage2Verdict(valid=True),
        ],
    )
    answer_question(
        client,
        "rushing TDs",
        conn=conn,
        schema_text=fake_schema_text,
    )
    retry_prompt = str(client.create_calls[1]["messages"])
    # every issue, not just the suggested_fix
    assert "Returns an id, not a readable name." in retry_prompt
    assert "No minimum-games threshold." in retry_prompt
    assert "Group by the name column and add HAVING." in retry_prompt
    # and the SQL that was rejected
    assert "COUNT(*) AS tds" in retry_prompt


def test_retry_uses_the_stronger_model(tiny_db, fake_schema_text):
    from nfl_chatdb.stage1_sql import STAGE1_MODEL, STAGE1_RETRY_MODEL

    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_ALL_TD, SQL_OK],
        verdicts=[
            Stage2Verdict(valid=False, issues=["wrong"], suggested_fix="fix"),
            Stage2Verdict(valid=True),
        ],
    )
    answer_question(
        client,
        "rushing TDs",
        conn=conn,
        schema_text=fake_schema_text,
    )
    assert client.create_calls[0]["model"] == STAGE1_MODEL
    assert client.create_calls[1]["model"] == STAGE1_RETRY_MODEL


def test_no_retry_when_stage2_says_not_worthwhile(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_OK],
        verdicts=[
            Stage2Verdict(
                valid=False,
                issues=["'best' is undefined; any choice is defensible."],
                retry_worthwhile=False,
            ),
            AnswerSummary(answer="'best' is subjective here.", reliable=False),
        ],
    )
    out = answer_question(
        client,
        "who is the best QB?",
        conn=conn,
        schema_text=fake_schema_text,
    )
    assert out.semantic_retries == 0
    assert out.reliable is False
    # only the one Stage 1 call — no retry round
    assert len(client.create_calls) == 1


def test_still_invalid_after_retry_is_caveated(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_ALL_TD, SQL_ALL_TD],
        verdicts=[
            Stage2Verdict(
                valid=False, issues=["Still wrong."], suggested_fix="Try harder."
            ),
            AnswerSummary(answer="This still looks off.", reliable=False),
        ],
    )
    out = answer_question(
        client,
        "rushing TDs",
        conn=conn,
        schema_text=fake_schema_text,
    )
    assert out.semantic_retries == 1
    assert out.reliable is False
    # a retry supersedes the verdict, so stage2_valid is neither True nor False
    assert out.stage2_valid is None


def test_usage_and_timing_are_tracked(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_OK],
        verdicts=[Stage2Verdict(valid=True), AnswerSummary(answer="x", reliable=True)],
    )
    out = answer_question(
        client,
        "rushing TDs",
        conn=conn,
        schema_text=fake_schema_text,
    )
    # Stage 1 (create) + Stage 2 + Stage 3 (parse) = 3 calls
    assert out.usage.calls == 3
    assert out.elapsed_s >= 0.0


def test_stage3_answer_and_reliable_reach_the_outcome(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_OK],
        verdicts=[
            Stage2Verdict(valid=True),
            AnswerSummary(answer="Two rushing TDs in 2023.", reliable=True),
        ],
    )
    out = answer_question(
        client,
        "rushing TDs in 2023",
        conn=conn,
        schema_text=fake_schema_text,
    )
    assert out.answer == "Two rushing TDs in 2023."
    assert out.reliable is True
    assert out.reliable is True


def test_no_second_stage2_after_a_retry(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_ALL_TD, SQL_OK],
        verdicts=[
            Stage2Verdict(valid=False, issues=["wrong"], suggested_fix="fix"),
            AnswerSummary(answer="ok", reliable=True),
        ],
    )
    answer_question(
        client,
        "rushing TDs",
        conn=conn,
        schema_text=fake_schema_text,
    )
    # one Stage 2 verify + one Stage 3 synthesize — the retry does not re-verify
    parse_models = [c["model"] for c in client.parse_calls]
    assert parse_models == ["claude-sonnet-5", "claude-haiku-4-5"]


def test_answer_question_reports_progress(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_ALL_TD, SQL_OK],
        verdicts=[
            Stage2Verdict(valid=False, issues=["wrong"], suggested_fix="fix it"),
        ],
    )
    seen = []
    answer_question(
        client,
        "rushing TDs",
        conn=conn,
        schema_text=fake_schema_text,
        on_progress=seen.append,
    )
    assert seen[0] == "Writing SQL"
    assert any("Refining" in m for m in seen)
    assert seen[-1] == "Writing the answer"


# generate_sql retries an empty result once internally, so a persistently
# empty query costs two `create` calls before answer_question sees it.
def test_empty_caveated_result_falls_back_to_raw_rows(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_EMPTY, SQL_STILL_EMPTY, SQL_RAW_ROWS],
        verdicts=[
            Stage2Verdict(valid=False, issues=["Returns 0 rows; over-filtered."]),
        ],
    )
    out = answer_question(
        client,
        "which back had the best 1999 season?",
        conn=conn,
        schema_text=fake_schema_text,
        max_semantic_retries=0,
    )
    assert out.result.row_count > 0
    assert out.fallback_note and "match" in out.fallback_note.lower()
    assert out.sql == (
        "SELECT rusher_player_name, rush_touchdown "
        "FROM play_by_play WHERE season = 2023 LIMIT 50"
    )


def test_fallback_note_when_nothing_matches(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_EMPTY, SQL_STILL_EMPTY, SQL_STILL_EMPTY],
        verdicts=[Stage2Verdict(valid=False, issues=["0 rows."])],
    )
    out = answer_question(
        client,
        "q",
        conn=conn,
        schema_text=fake_schema_text,
        max_semantic_retries=0,
    )
    assert out.result.row_count == 0
    assert out.fallback_note == "No records in the database match this situation."


def test_no_fallback_when_result_is_non_empty(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_OK],
        verdicts=[Stage2Verdict(valid=True)],
    )
    out = answer_question(
        client,
        "rushing TDs",
        conn=conn,
        schema_text=fake_schema_text,
    )
    assert out.fallback_note is None


def test_no_fallback_when_empty_but_stage2_accepts(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_STILL_EMPTY, SQL_STILL_EMPTY],
        verdicts=[Stage2Verdict(valid=True)],
    )
    out = answer_question(
        client,
        "any 1999 rushers?",
        conn=conn,
        schema_text=fake_schema_text,
    )
    assert out.result.row_count == 0
    assert out.fallback_note is None
    # the two internal Stage 1 attempts, and no fallback query beyond them
    assert len(client.create_calls) == 2


def test_stage1_failure_returns_a_degraded_outcome(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[
            "```sql\nSELECT bad_a FROM play_by_play\n```",
            "```sql\nSELECT bad_b FROM play_by_play\n```",
        ],
        verdicts=[],
    )
    out = answer_question(client, "q", conn=conn, schema_text=fake_schema_text)
    assert out.reliable is False
    assert "couldn't answer" in out.answer.lower()
    assert out.result.row_count == 0
    assert out.stage1_attempts == 0


def test_api_failure_in_stage1_returns_a_degraded_outcome(tiny_db, fake_schema_text):
    import anthropic

    class Boom:
        class messages:
            @staticmethod
            def create(**_kw):
                raise anthropic.AnthropicError("connection reset")

    out = answer_question(
        Boom(), "q", conn=connect(tiny_db), schema_text=fake_schema_text
    )
    assert out.reliable is False
    assert "couldn't answer" in out.answer.lower()
