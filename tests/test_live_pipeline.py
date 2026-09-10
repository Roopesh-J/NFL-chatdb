"""Live end-to-end smoke tests. Run with: uv run pytest -m live tests/test_live_pipeline.py

Requires data/nfl.db (uv run python -m nfl_chatdb.ingest) and ANTHROPIC_API_KEY.
"""

import os

import pytest

from nfl_chatdb.client import build_client
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
    from dotenv import load_dotenv

    load_dotenv()
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
    assert out.stage2_valid is not False
    assert out.reliable


def test_pipeline_never_raises_on_a_vague_question(client, conn, schema_text):
    # Should still return something (possibly flagged), not blow up.
    out = answer_question(
        client,
        "Who was the best quarterback in 2023?",
        conn=conn,
        schema_text=schema_text,
    )
    assert out.sql.lower().lstrip().startswith(("select", "with"))
