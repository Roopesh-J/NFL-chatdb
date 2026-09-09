import sqlite3

import pandas as pd
import pytest

from nfl_chatdb.ingest import (
    SEASONS,
    TABLES,
    add_player_display_name,
    render_schema_snapshot,
    write_dataframe,
)


def test_seasons_are_last_four_completed():
    # 2025 excluded: nflverse-data hasn't published its player_stats
    # release asset for 2025 yet as of this ingest run.
    assert SEASONS == [2021, 2022, 2023, 2024]


def test_tables_constant():
    assert TABLES == ("play_by_play", "seasonal_stats", "snap_counts")


def test_write_dataframe_replaces_and_counts(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        n = write_dataframe(df, "play_by_play", conn)
        assert n == 3
        # replace semantics: writing again does not append
        n2 = write_dataframe(df, "play_by_play", conn)
        assert n2 == 3
        assert (
            conn.execute("SELECT COUNT(*) FROM play_by_play").fetchone()[0] == 3
        )
    finally:
        conn.close()


def test_render_schema_snapshot_format(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        conn.executescript(
            """
            CREATE TABLE play_by_play (play_id INTEGER, game_id TEXT);
            CREATE TABLE seasonal_stats (player_id TEXT, rushing_tds INTEGER);
            CREATE TABLE snap_counts (pfr_player_id TEXT, offense_snaps INTEGER);
            """
        )
        snapshot = render_schema_snapshot(conn)
    finally:
        conn.close()
    assert "Table: play_by_play" in snapshot
    assert "  play_id (INTEGER)" in snapshot
    assert "  game_id (TEXT)" in snapshot
    assert "Table: seasonal_stats" in snapshot
    assert "Table: snap_counts" in snapshot
    # blocks separated by a blank line
    assert "\n\nTable: seasonal_stats" in snapshot


def test_render_schema_snapshot_lists_low_cardinality_text_values(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        conn.executescript(
            """
            CREATE TABLE play_by_play (
                game_half TEXT, play_desc TEXT, posteam TEXT, yards_gained INTEGER
            );
            CREATE TABLE seasonal_stats (player_id TEXT, rushing_tds INTEGER);
            CREATE TABLE snap_counts (pfr_player_id TEXT, offense_snaps INTEGER);
            """
        )
        rows = (
            [("Half1", f"a play {i}", "TEN", i) for i in range(40)]
            + [("Half2", f"b play {i}", "KC", i) for i in range(40)]
            + [("Overtime", f"c play {i}", "SF", i) for i in range(6)]
        )
        conn.executemany("INSERT INTO play_by_play VALUES (?,?,?,?)", rows)
        conn.commit()
        snapshot = render_schema_snapshot(conn)
    finally:
        conn.close()

    assert (
        "game_half (TEXT) -- values: 'Half1', 'Half2', 'Overtime'" in snapshot
    )
    # free-text column: too many distinct values, no list
    assert "play_desc (TEXT) --" not in snapshot
    # numeric column: untouched
    assert "  yards_gained (INTEGER)" in snapshot


def test_render_schema_snapshot_skips_sparse_text_columns(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        conn.executescript(
            """
            CREATE TABLE play_by_play (game_half TEXT, lateral_name TEXT);
            CREATE TABLE seasonal_stats (player_id TEXT);
            CREATE TABLE snap_counts (pfr_player_id TEXT);
            """
        )
        rows = [("Half1", None)] * 4000 + [
            ("Half2", name) for name in ("A.Smith", "B.Jones", "C.Lee")
        ]
        conn.executemany("INSERT INTO play_by_play VALUES (?,?)", rows)
        conn.commit()
        snapshot = render_schema_snapshot(conn)
    finally:
        conn.close()
    # dense categorical: listed
    assert "game_half (TEXT) -- values: 'Half1', 'Half2'" in snapshot
    # 3 non-null rows out of ~4000: below the coverage floor, not listed
    assert "lateral_name (TEXT) --" not in snapshot
    assert "  lateral_name (TEXT)\n" in snapshot


def test_render_schema_snapshot_skips_high_cardinality_text(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        conn.executescript(
            """
            CREATE TABLE play_by_play (label TEXT);
            CREATE TABLE seasonal_stats (player_id TEXT);
            CREATE TABLE snap_counts (pfr_player_id TEXT);
            """
        )
        conn.executemany(
            "INSERT INTO play_by_play VALUES (?)",
            [(f"v{i}",) for i in range(40)],
        )
        conn.commit()
        snapshot = render_schema_snapshot(conn)
    finally:
        conn.close()
    assert "  label (TEXT)\n" in snapshot
    assert "label (TEXT) --" not in snapshot


def test_add_player_display_name_merges_on_season_and_id():
    seasonal = pd.DataFrame(
        {
            "player_id": ["00-0001", "00-0002", "00-9999"],
            "season": [2023, 2023, 2023],
            "rushing_tds": [5, 1, 0],
        }
    )
    rosters = pd.DataFrame(
        {
            "season": [2023, 2023, 2022],
            "player_id": ["00-0001", "00-0002", "00-0001"],
            "player_name": ["Derrick Henry", "Tyreek Hill", "Old Name"],
            "team": ["TEN", "MIA", "TEN"],
        }
    )
    merged = add_player_display_name(seasonal, rosters)
    names = merged.set_index("player_id")["player_display_name"]
    assert names["00-0001"] == "Derrick Henry"
    assert names["00-0002"] == "Tyreek Hill"
    assert pd.isna(names["00-9999"])
    # no row duplication from the merge
    assert len(merged) == len(seasonal)


@pytest.mark.live
def test_ingest_end_to_end(tmp_path):
    """Real pull from nflverse for a single season — slow, network."""
    from nfl_chatdb.database import connect, run_query
    from nfl_chatdb.ingest import ingest

    db_path = tmp_path / "nfl.db"
    counts = ingest(
        db_path=db_path,
        seasons=[2023],
        snapshot_path=tmp_path / "schema_snapshot.txt",
    )
    assert counts["play_by_play"] > 40000  # ~48k plays in a season
    assert counts["seasonal_stats"] > 500
    assert counts["snap_counts"] > 5000

    conn = connect(db_path)
    # ingest-time enrichment: seasonal_stats gains a display-name column,
    # and the large majority of rows resolve to a name.
    named = run_query(
        conn,
        "SELECT COUNT(*) FROM seasonal_stats "
        "WHERE player_display_name IS NOT NULL",
    )
    assert named.rows[0][0] > 400
    henry = run_query(
        conn,
        "SELECT SUM(rushing_tds) FROM seasonal_stats "
        "WHERE player_display_name = 'Derrick Henry' AND season = 2023",
    )
    assert henry.rows[0][0] and henry.rows[0][0] > 0
