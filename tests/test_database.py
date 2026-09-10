import sqlite3

import pytest

from nfl_chatdb.database import (
    MAX_RESULT_ROWS,
    QueryError,
    QueryResult,
    connect,
    run_query,
)


def test_run_query_returns_rows_and_count(tiny_db):
    conn = connect(tiny_db)
    result = run_query(
        conn,
        "SELECT rusher_player_name, SUM(rush_touchdown) AS tds "
        "FROM play_by_play WHERE season = 2023 GROUP BY rusher_player_name",
    )
    assert isinstance(result, QueryResult)
    assert result.columns == ["rusher_player_name", "tds"]
    assert result.rows == [("D.Henry", 2)]
    assert result.row_count == 1


def test_run_query_rejects_non_select(tiny_db):
    conn = connect(tiny_db)
    with pytest.raises(QueryError):
        run_query(conn, "DROP TABLE play_by_play")


def test_run_query_rejects_multiple_statements(tiny_db):
    conn = connect(tiny_db)
    with pytest.raises(QueryError):
        run_query(conn, "SELECT 1; SELECT 2")


def test_run_query_allows_with_cte(tiny_db):
    conn = connect(tiny_db)
    result = run_query(
        conn,
        "WITH x AS (SELECT * FROM play_by_play WHERE season = 2023) "
        "SELECT COUNT(*) AS n FROM x",
    )
    assert result.rows == [(5,)]


def test_run_query_ignores_double_dash_inside_a_string_literal(tiny_db):
    conn = connect(tiny_db)
    result = run_query(
        conn,
        "SELECT COUNT(*) AS n FROM play_by_play "
        "WHERE game_id = '2023_01_A_B' OR game_id = 'x--y'",
    )
    assert result.rows == [(2,)]


def test_run_query_skips_a_leading_comment(tiny_db):
    conn = connect(tiny_db)
    result = run_query(conn, "-- count 2023 plays\nSELECT COUNT(*) FROM play_by_play")
    assert result.rows == [(5,)]


def test_run_query_wraps_sqlite_errors(tiny_db):
    conn = connect(tiny_db)
    with pytest.raises(QueryError) as excinfo:
        run_query(conn, "SELECT no_such_column FROM play_by_play")
    assert "no_such_column" in str(excinfo.value)


def test_connect_missing_file_raises(tmp_path):
    with pytest.raises(QueryError):
        connect(tmp_path / "does_not_exist.db")


def test_connect_rejects_directory(tmp_path):
    # A directory used to pass `.exists()` and then throw a bare
    # OperationalError; it must raise QueryError like a missing file.
    with pytest.raises(QueryError):
        connect(tmp_path)


def test_run_query_truncates_large_result(tmp_path):
    path = tmp_path / "big.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE big (n INTEGER);
        WITH RECURSIVE seq(n) AS (
            SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 10005
        )
        INSERT INTO big SELECT n FROM seq;
        """
    )
    conn.commit()
    conn.close()

    ro = connect(path)
    result = run_query(ro, "SELECT * FROM big")
    assert len(result.rows) == MAX_RESULT_ROWS
    assert result.row_count == MAX_RESULT_ROWS
    assert result.truncated is True

    small = run_query(ro, "SELECT * FROM big WHERE n <= 5")
    assert len(small.rows) == 5
    assert small.truncated is False


def test_run_query_times_out_on_slow_query(tiny_db):
    conn = connect(tiny_db)
    # A recursive CTE that would take far longer than the limit to finish.
    slow = (
        "WITH RECURSIVE seq(n) AS ("
        "  SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 100000000"
        ") SELECT COUNT(*) FROM seq"
    )
    with pytest.raises(QueryError) as excinfo:
        run_query(conn, slow, timeout_seconds=0.3)
    assert "time" in str(excinfo.value).lower()


def test_run_query_timeout_does_not_affect_fast_queries(tiny_db):
    conn = connect(tiny_db)
    result = run_query(conn, "SELECT COUNT(*) FROM play_by_play", timeout_seconds=0.3)
    assert result.rows == [(5,)]
    # the progress handler is cleared afterwards, so a later query is unbounded
    again = run_query(conn, "SELECT COUNT(*) FROM play_by_play")
    assert again.rows == [(5,)]


def test_connection_is_read_only(tiny_db):
    conn = connect(tiny_db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO play_by_play (play_id) VALUES (99)")
