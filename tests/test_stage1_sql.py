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


def test_extract_sql_language_tagged_fences():
    assert extract_sql("```sqlite\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("```postgresql\nSELECT 1\n```") == "SELECT 1"


def test_extract_sql_fence_and_query_on_one_line():
    # no newline after the opening fence — `SELECT` must not be eaten as a tag
    assert extract_sql("```SELECT n FROM t WHERE x = 1```") == "SELECT n FROM t WHERE x = 1"
    assert extract_sql("```WITH a AS (SELECT 1) SELECT * FROM a```") == (
        "WITH a AS (SELECT 1) SELECT * FROM a"
    )


def test_extract_sql_empty_tag_line():
    assert extract_sql("```\nSELECT 1\n```") == "SELECT 1"


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
