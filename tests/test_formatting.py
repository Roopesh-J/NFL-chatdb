from nfl_chatdb.database import QueryResult
from nfl_chatdb.formatting import format_result_sample, outcome_to_dict
from nfl_chatdb.pipeline import PipelineOutcome


def _outcome(**over) -> PipelineOutcome:
    base = dict(
        question="q",
        sql="SELECT 1",
        result=QueryResult(columns=["a"], rows=[(1,)], row_count=1),
        answer="One.",
        reliable=True,
        stage1_attempts=1,
        semantic_retries=0,
        stage2_valid=True,
        stage2_issues=[],
    )
    base.update(over)
    return PipelineOutcome(**base)  # type: ignore[arg-type]


def test_outcome_to_dict_shape():
    assert outcome_to_dict(_outcome()) == {
        "question": "q",
        "sql": "SELECT 1",
        "columns": ["a"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": False,
        "answer": "One.",
        "reliable": True,
        "stage1_attempts": 1,
        "semantic_retries": 0,
        "stage2_valid": True,
        "stage2_issues": [],
        "fallback_note": None,
        "elapsed_s": 0.0,
        "cost_usd": 0.0,
        "model_calls": 0,
    }


def test_outcome_to_dict_surfaces_stage2_and_reliability():
    d = outcome_to_dict(
        _outcome(
            reliable=False,
            stage2_valid=False,
            stage2_issues=["wrong season"],
            result=QueryResult(columns=["a"], rows=[(1,)], row_count=1, truncated=True),
        )
    )
    assert d["truncated"] is True
    assert d["reliable"] is False
    assert d["stage2_valid"] is False
    assert d["stage2_issues"] == ["wrong season"]


def test_outcome_to_dict_coerces_non_json_cells():
    d = outcome_to_dict(
        _outcome(
            result=QueryResult(columns=["blob"], rows=[(b"\x00\x01",)], row_count=1)
        )
    )
    assert d["rows"] == [["b'\\x00\\x01'"]]


def test_format_small_result():
    r = QueryResult(columns=["name", "tds"], rows=[("D.Henry", 12)], row_count=1)
    out = format_result_sample(r)
    assert out.splitlines()[0] == "1 row(s)."
    assert "name | tds" in out
    assert "D.Henry | 12" in out


def test_format_empty_result():
    r = QueryResult(columns=["name"], rows=[], row_count=0)
    assert format_result_sample(r).splitlines()[0] == "0 rows."


def test_format_truncates_and_reports_total():
    rows = [(f"p{i}", i) for i in range(20)]
    r = QueryResult(columns=["name", "n"], rows=rows, row_count=20)
    out = format_result_sample(r, max_rows=5)
    assert out.splitlines()[0] == "20 row(s). Showing first 5:"
    assert len(out.splitlines()) == 7
    assert "p4 | 4" in out
    assert "p5 | 5" not in out


def test_format_renders_null():
    r = QueryResult(columns=["a", "b"], rows=[(1, None)], row_count=1)
    assert "1 | NULL" in format_result_sample(r)
