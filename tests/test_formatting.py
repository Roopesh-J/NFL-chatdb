from nfl_chatdb.database import QueryResult
from nfl_chatdb.formatting import format_result_sample


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
    # header + 5 data rows + the header line
    assert len(out.splitlines()) == 7
    assert "p4 | 4" in out
    assert "p5 | 5" not in out


def test_format_renders_null():
    r = QueryResult(columns=["a", "b"], rows=[(1, None)], row_count=1)
    assert "1 | NULL" in format_result_sample(r)
