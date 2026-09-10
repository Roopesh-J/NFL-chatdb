import pydantic

from nfl_chatdb.stage2_validate import Stage2Verdict
from nfl_chatdb.stage3_answer import (
    STAGE3_MODEL,
    AnswerSummary,
    synthesize_answer,
)


class _ParseResponse:
    def __init__(self, summary):
        self.parsed_output = summary


class FakeClient:
    def __init__(self, summary):
        self._summary = summary
        self.calls = []
        client = self

        class _M:
            def parse(self, **kwargs):
                client.calls.append(kwargs)
                if isinstance(client._summary, Exception):
                    raise client._summary
                return _ParseResponse(client._summary)

        self.messages = _M()


def test_model_constant():
    assert STAGE3_MODEL == "claude-haiku-4-5"


def test_synthesize_returns_summary():
    summary = AnswerSummary(answer="Mahomes, 33-11 (.750).", reliable=True)
    client = FakeClient(summary)
    out = synthesize_answer(
        client, "best QB record in one-score games?",
        "SELECT ...", "1 row(s).\nqb | w | l\nMahomes | 33 | 11",
        Stage2Verdict(valid=True),
    )
    assert out is summary
    assert client.calls[0]["model"] == STAGE3_MODEL


def test_verdict_issues_reach_the_prompt_when_invalid():
    client = FakeClient(AnswerSummary(answer="x", reliable=False))
    synthesize_answer(
        client, "q", "SELECT 1", "1 row",
        Stage2Verdict(valid=False, issues=["sample of one game"]),
    )
    sent = str(client.calls[0]["messages"])
    assert "sample of one game" in sent


def test_parse_failure_falls_back_to_verdict():
    err = pydantic.ValidationError.from_exception_data("AnswerSummary", [])
    client = FakeClient(err)
    out = synthesize_answer(
        client, "q", "SELECT 1", "1 row", Stage2Verdict(valid=True),
    )
    assert out.answer == ""
    assert out.reliable is True

    out2 = synthesize_answer(
        client, "q", "SELECT 1", "0 rows", Stage2Verdict(valid=False),
    )
    assert out2.reliable is False


def test_none_output_falls_back():
    client = FakeClient(None)
    out = synthesize_answer(
        client, "q", "SELECT 1", "1 row", Stage2Verdict(valid=True),
    )
    assert out.answer == ""
    assert out.reliable is True
