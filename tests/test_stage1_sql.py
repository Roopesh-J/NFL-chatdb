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
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


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


def test_extract_sql_language_tagged_fences():
    assert extract_sql("```sqlite\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("```postgresql\nSELECT 1\n```") == "SELECT 1"


def test_extract_sql_handles_every_fence_shape():
    # bare fence + SQL on one line
    assert extract_sql("```SELECT n FROM t```") == "SELECT n FROM t"
    # language tag + SQL on one line
    assert extract_sql("```sql SELECT n FROM t```") == "SELECT n FROM t"
    # empty tag line
    assert extract_sql("```\nSELECT 1\n```") == "SELECT 1"
    # leading WITH
    assert extract_sql("```sql\nWITH a AS (SELECT 1) SELECT * FROM a\n```") == (
        "WITH a AS (SELECT 1) SELECT * FROM a"
    )
    # prose before the fence
    assert extract_sql("Here:\n```sql\nSELECT 1\n```\nHope that helps") == "SELECT 1"


def test_extract_sql_empty_raises():
    with pytest.raises(Stage1Error):
        extract_sql("   ")


def test_generate_sql_succeeds_first_try(tiny_db, fake_schema_text):
    conn = connect(tiny_db)
    client = FakeClient(
        [
            "```sql\nSELECT SUM(rush_touchdown) AS tds FROM play_by_play "
            "WHERE season = 2023\n```"
        ]
    )
    out = generate_sql(client, "How many rushing TDs in 2023?", fake_schema_text, conn)
    assert isinstance(out, Stage1Result)
    assert out.attempts == 1
    assert out.degenerate is False
    assert out.result.rows == [(2,)]
    call = client.messages.calls[0]
    assert call["model"] == STAGE1_MODEL
    # schema rides a cached system block, not the (volatile) user message
    assert call["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "seasonal_stats" in call["system"][0]["text"]
    assert "seasonal_stats" not in str(call["messages"])


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


def test_generate_sql_passes_retry_context_into_prompt(tiny_db, fake_schema_text):
    from nfl_chatdb.stage1_sql import RetryContext

    conn = connect(tiny_db)
    client = FakeClient(
        ["```sql\nSELECT SUM(rush_touchdown) AS tds FROM play_by_play\n```"]
    )
    generate_sql(
        client,
        "q",
        fake_schema_text,
        conn,
        retry=RetryContext(
            correction="Only count rushing TDs, not receiving TDs.",
            previous_sql="SELECT COUNT(*) FROM play_by_play",
            previous_sample="1 row(s).\nn\n5",
        ),
    )
    first = str(client.messages.calls[0]["messages"])
    assert "receiving TDs" in first
    assert "SELECT COUNT(*) FROM play_by_play" in first
