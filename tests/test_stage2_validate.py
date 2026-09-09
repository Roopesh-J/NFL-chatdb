import pydantic

from nfl_chatdb.stage2_validate import (
    STAGE2_MODEL,
    Stage2Verdict,
    validate_semantics,
)


class _ParseResponse:
    def __init__(self, verdict):
        self.parsed_output = verdict


class FakeParseMessages:
    def __init__(self, verdict):
        self._verdict = verdict
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _ParseResponse(self._verdict)


class FakeParseClient:
    def __init__(self, verdict):
        self.messages = FakeParseMessages(verdict)


def test_model_constant():
    assert STAGE2_MODEL == "claude-sonnet-5"


def test_verdict_defaults():
    v = Stage2Verdict(valid=True)
    assert v.issues == []
    assert v.suggested_fix is None


def test_validate_semantics_returns_parsed_verdict(fake_schema_text):
    verdict = Stage2Verdict(
        valid=False,
        issues=["Counts all TDs, not just rushing TDs."],
        suggested_fix="Filter to rush_touchdown = 1.",
    )
    client = FakeParseClient(verdict)
    out = validate_semantics(
        client,
        question="How many rushing TDs did D.Henry score in 2023?",
        sql="SELECT COUNT(*) FROM play_by_play WHERE touchdown = 1",
        schema_text=fake_schema_text,
        result_sample="1 row(s).\ncnt\n30",
    )
    assert out is verdict
    call = client.messages.calls[0]
    assert call["model"] == STAGE2_MODEL
    assert call["output_format"] is Stage2Verdict
    # the prompt carries all four inputs
    sent = str(call["messages"])
    assert "rushing TDs" in sent
    assert "touchdown = 1" in sent
    assert "cnt" in sent


def test_validate_semantics_handles_unparseable_verdict(fake_schema_text):
    # messages.parse returns parsed_output=None on refusal / max_tokens.
    client = FakeParseClient(None)
    out = validate_semantics(
        client,
        question="q",
        sql="SELECT 1",
        schema_text=fake_schema_text,
        result_sample="1 row(s).\nn\n1",
    )
    assert isinstance(out, Stage2Verdict)
    assert out.valid is False
    assert out.issues


class _RaisingParseMessages:
    def parse(self, **kwargs):
        # Same failure the SDK raises when the JSON verdict is truncated
        # at max_tokens: validate_json on an incomplete string.
        pydantic.TypeAdapter(Stage2Verdict).validate_json('{"valid": true, "iss')


class _RaisingParseClient:
    messages = _RaisingParseMessages()


def test_validate_semantics_handles_truncated_json(fake_schema_text):
    out = validate_semantics(
        _RaisingParseClient(),
        question="q",
        sql="SELECT 1",
        schema_text=fake_schema_text,
        result_sample="1 row(s).\nn\n1",
    )
    assert isinstance(out, Stage2Verdict)
    assert out.valid is False
    assert out.issues
