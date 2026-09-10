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
        client, "rushing TDs", conn=conn, schema_text=fake_schema_text,
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
        client, "rushing TDs", conn=conn, schema_text=fake_schema_text,
    )
    assert client.create_calls[0]["model"] == STAGE1_MODEL
    assert client.create_calls[1]["model"] == STAGE1_RETRY_MODEL


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


def test_answer_question_reports_progress(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = ScriptedClient(
        create_replies=[SQL_ALL_TD, SQL_OK],
        verdicts=[
            Stage2Verdict(valid=False, issues=["wrong"], suggested_fix="fix it"),
            Stage2Verdict(valid=True),
        ],
    )
    seen = []
    answer_question(
        client, "rushing TDs", conn=conn, schema_text=fake_schema_text,
        on_progress=seen.append,
    )
    assert seen[0] == "Writing SQL"
    assert any("Refining" in m for m in seen)
    assert seen[-1] == "Re-checking the answer"


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
