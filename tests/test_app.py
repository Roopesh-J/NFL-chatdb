import anthropic

from nfl_chatdb.app import Api
from nfl_chatdb.stage2_validate import Stage2Verdict
from tests.test_pipeline import ScriptedClient

SQL_OK = (
    "```sql\nSELECT SUM(rush_touchdown) AS tds "
    "FROM play_by_play WHERE season = 2023\n```"
)
SQL_BAD = "```sql\nSELECT no_such_col FROM play_by_play\n```"


def test_ask_happy_path(tiny_db, fake_schema_text):
    client = ScriptedClient(
        create_replies=[SQL_OK], verdicts=[Stage2Verdict(valid=True)]
    )
    api = Api(client, fake_schema_text, tiny_db)
    d = api.ask("How many rushing TDs in 2023?")
    assert "error" not in d
    assert d["rows"] == [[2]]
    assert set(d) >= {
        "sql",
        "columns",
        "rows",
        "row_count",
        "answer",
        "reliable",
        "stage1_attempts",
        "truncated",
    }


def test_ask_blank_question_returns_error(tiny_db, fake_schema_text):
    client = ScriptedClient(create_replies=[], verdicts=[])
    api = Api(client, fake_schema_text, tiny_db)
    assert api.ask("   ")["error"]
    assert client.create_calls == []


def test_ask_missing_db_returns_error(tmp_path, fake_schema_text):
    api = Api(object(), fake_schema_text, tmp_path / "absent.db")
    assert "not found" in api.ask("anything")["error"]


def test_ask_stage1_failure_is_a_degraded_answer_not_an_error(
    tiny_db, fake_schema_text
):
    client = ScriptedClient(create_replies=[SQL_BAD, SQL_BAD], verdicts=[])
    api = Api(client, fake_schema_text, tiny_db)
    d = api.ask("anything")
    assert "error" not in d
    assert d["reliable"] is False
    assert "couldn't answer" in d["answer"].lower()


def test_ask_catches_an_unexpected_exception(monkeypatch, tiny_db, fake_schema_text):
    import nfl_chatdb.app as appmod

    def boom(*_a, **_k):
        raise RuntimeError("something odd")

    monkeypatch.setattr(appmod, "answer_question", boom)
    api = Api(object(), fake_schema_text, tiny_db)
    assert "something odd" in api.ask("anything")["error"]


def test_main_missing_db_exits_nonzero(tmp_path, monkeypatch, capsys, fake_schema_text):
    import nfl_chatdb.app as appmod

    monkeypatch.setattr(appmod, "load_schema_text", lambda: fake_schema_text)
    monkeypatch.setattr(appmod, "build_client", lambda: object())
    monkeypatch.setattr(appmod, "DEFAULT_DB_PATH", tmp_path / "nope.db")

    rc = appmod.main([])
    assert rc != 0
    assert "ingest" in capsys.readouterr().out


def test_main_missing_key_exits_nonzero(monkeypatch, capsys, fake_schema_text):
    import nfl_chatdb.app as appmod

    monkeypatch.setattr(appmod, "load_schema_text", lambda: fake_schema_text)

    def no_key():
        raise anthropic.AnthropicError("no api key")

    monkeypatch.setattr(appmod, "build_client", no_key)
    assert appmod.main([]) != 0
