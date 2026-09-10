import json

import anthropic

from nfl_chatdb import cli
from nfl_chatdb.database import QueryResult
from nfl_chatdb.pipeline import PipelineOutcome


def _outcome(**over) -> PipelineOutcome:
    base = dict(
        question="q",
        sql="SELECT 1 AS n",
        result=QueryResult(columns=["n"], rows=[(1,)], row_count=1),
        answer="One.",
        reliable=True,
        stage1_attempts=1,
        semantic_retries=0,
        stage2_valid=True,
        stage2_issues=[],
    )
    base.update(over)
    return PipelineOutcome(**base)  # type: ignore[arg-type]


def test_render_outcome_shows_answer_and_two_stage_story():
    text = cli.render_outcome(_outcome())
    assert text.startswith("One.")
    assert "SELECT 1 AS n" in text
    assert "Stage 1: wrote SQL (1 attempt(s))" in text
    assert "Stage 2: valid" in text
    assert "Stage 3: reliable" in text


def test_render_outcome_after_a_retry_reports_it_not_stale_issues():
    text = cli.render_outcome(
        _outcome(
            reliable=False, semantic_retries=1, stage2_valid=None, stage2_issues=[]
        )
    )
    assert "retried 1x" in text
    assert "Stage 3: not reliable" in text


def test_render_outcome_stage2_flagged_no_retry():
    text = cli.render_outcome(
        _outcome(
            reliable=False,
            stage2_valid=False,
            stage2_issues=["Only counts rushing TDs."],
        )
    )
    assert "Stage 2: flagged — Only counts rushing TDs." in text


def test_main_happy_path(monkeypatch, tiny_db, capsys):
    monkeypatch.setattr(cli, "build_client", lambda: object())
    monkeypatch.setattr(cli, "load_schema_text", lambda: "Table: play_by_play")
    monkeypatch.setattr(cli, "answer_question", lambda *a, **k: _outcome())
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
    assert payload["reliable"] is True


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


def test_main_pipeline_failure_still_exits_0_with_a_degraded_answer(
    monkeypatch, tiny_db, capsys
):
    # answer_question never raises for model issues — it returns an outcome.
    monkeypatch.setattr(cli, "build_client", lambda: object())
    monkeypatch.setattr(cli, "load_schema_text", lambda: "schema")
    monkeypatch.setattr(
        cli,
        "answer_question",
        lambda *a, **k: _outcome(
            answer="I couldn't answer that: the model call failed",
            reliable=False,
        ),
    )
    rc = cli.main(["q", "--db", str(tiny_db)])
    assert rc == 0
    assert "couldn't answer" in capsys.readouterr().out.lower()
