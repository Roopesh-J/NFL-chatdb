import json

import anthropic

from nfl_chatdb import cli
from nfl_chatdb.database import QueryResult
from nfl_chatdb.pipeline import PipelineOutcome
from nfl_chatdb.stage1_sql import Stage1Error
from nfl_chatdb.stage2_validate import Stage2Verdict


def _outcome(caveated=False):
    return PipelineOutcome(
        question="q",
        sql="SELECT 1 AS n",
        result=QueryResult(columns=["n"], rows=[(1,)], row_count=1),
        verdict=Stage2Verdict(
            valid=not caveated,
            issues=["Only counts rushing TDs."] if caveated else [],
        ),
        caveated=caveated,
        semantic_retries=1 if caveated else 0,
        stage1_attempts=1,
        answer="One.",
        reliable=not caveated,
    )


def test_render_outcome_shows_answer_and_two_stage_story():
    text = cli.render_outcome(_outcome())
    assert text.startswith("One.")
    assert "SELECT 1 AS n" in text
    assert "Stage 1: wrote SQL" in text
    assert "Stage 2: valid" in text
    assert "Stage 3: reliable" in text


def test_render_outcome_caveated_shows_the_flag_and_issues():
    text = cli.render_outcome(_outcome(caveated=True))
    assert "then 1 semantic retry" in text
    assert "Stage 2: flagged — Only counts rushing TDs." in text
    assert "Stage 3: not reliable" in text


def test_main_happy_path(monkeypatch, tiny_db, capsys):
    monkeypatch.setattr(cli, "build_client", lambda: object())
    monkeypatch.setattr(cli, "load_schema_text", lambda: "Table: play_by_play")
    monkeypatch.setattr(
        cli, "answer_question", lambda *a, **k: _outcome()
    )
    rc = cli.main(["how many?", "--db", str(tiny_db)])
    assert rc == 0
    assert "SELECT 1 AS n" in capsys.readouterr().out


def test_main_json_output(monkeypatch, tiny_db, capsys):
    monkeypatch.setattr(cli, "build_client", lambda: object())
    monkeypatch.setattr(cli, "load_schema_text", lambda: "schema")
    monkeypatch.setattr(cli, "answer_question", lambda *a, **k: _outcome())
    rc = cli.main(["q", "--db", str(tiny_db), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sql"] == "SELECT 1 AS n"
    assert payload["caveated"] is False


def test_main_stage1_error_returns_1(monkeypatch, tiny_db, capsys):
    monkeypatch.setattr(cli, "build_client", lambda: object())
    monkeypatch.setattr(cli, "load_schema_text", lambda: "schema")

    def _boom(*a, **k):
        raise Stage1Error(last_sql="SELECT x", last_error="no such column: x")

    monkeypatch.setattr(cli, "answer_question", _boom)
    rc = cli.main(["q", "--db", str(tiny_db)])
    assert rc == 1
    assert "no such column: x" in capsys.readouterr().out


def test_main_missing_db_returns_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "build_client", lambda: object())
    rc = cli.main(["q", "--db", str(tmp_path / "absent.db")])
    assert rc == 2
    assert "ingest" in capsys.readouterr().out.lower()


def test_main_missing_key_returns_2_with_guidance(monkeypatch, tiny_db, capsys):
    monkeypatch.setattr(cli, "load_schema_text", lambda: "schema")

    def _no_key():
        raise anthropic.AnthropicError("could not resolve authentication")

    monkeypatch.setattr(cli, "build_client", _no_key)
    rc = cli.main(["q", "--db", str(tiny_db)])
    assert rc == 2
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out
    assert "Traceback" not in out


def test_main_api_error_during_answer_returns_2(monkeypatch, tiny_db, capsys):
    monkeypatch.setattr(cli, "load_schema_text", lambda: "schema")
    monkeypatch.setattr(cli, "build_client", lambda: object())

    def _boom(*a, **k):
        raise anthropic.APIError("rate limited", request=None, body=None)

    monkeypatch.setattr(cli, "answer_question", _boom)
    rc = cli.main(["q", "--db", str(tiny_db)])
    assert rc == 2
    assert "api call failed" in capsys.readouterr().out.lower()
