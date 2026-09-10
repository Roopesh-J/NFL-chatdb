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
    assert TABLES == (
        "play_by_play",
        "seasonal_stats",
        "weekly",
        "schedules",
        "rosters",
        "snap_counts",
    )


def test_render_schema_snapshot_trims_play_by_play_columns(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        conn.execute(
            "CREATE TABLE play_by_play ("
            "  game_id TEXT, epa REAL, yards_gained REAL,"
            "  total_home_raw_air_epa REAL, lateral_rusher_player_name TEXT,"
            "  fantasy_player_id TEXT"
            ")"
        )
        snapshot = render_schema_snapshot(conn)
    finally:
        conn.close()
    assert "  game_id (TEXT)" in snapshot
    assert "  epa (REAL)" in snapshot
    assert "  yards_gained (REAL)" in snapshot
    # not on the curated list
    assert "total_home_raw_air_epa" not in snapshot
    assert "lateral_rusher_player_name" not in snapshot
    assert "fantasy_player_id" not in snapshot


def test_render_schema_snapshot_keeps_other_tables_whole(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        conn.execute("CREATE TABLE weekly (player_id TEXT, some_obscure_col REAL)")
        snapshot = render_schema_snapshot(conn)
    finally:
        conn.close()
    assert "some_obscure_col" in snapshot  # only play_by_play is trimmed


def test_render_schema_snapshot_skips_absent_tables(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        conn.execute("CREATE TABLE schedules (game_id TEXT, result INTEGER)")
        snapshot = render_schema_snapshot(conn)
    finally:
        conn.close()
    assert "Table: schedules" in snapshot
    # the other five tables in TABLES are not in this db
    assert "Table: play_by_play" not in snapshot


def test_write_dataframe_replaces_and_counts(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        n = write_dataframe(df, "play_by_play", conn)
        assert n == 3
        # replace semantics: writing again does not append
        n2 = write_dataframe(df, "play_by_play", conn)
        assert n2 == 3
        assert conn.execute("SELECT COUNT(*) FROM play_by_play").fetchone()[0] == 3
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

    assert "game_half (TEXT) -- values: 'Half1', 'Half2', 'Overtime'" in snapshot
    # free-text column: too many distinct values, no list
    assert "play_desc (TEXT) --" not in snapshot
    # numeric column: untouched
    assert "  yards_gained (INTEGER)" in snapshot


def test_render_schema_snapshot_skips_sparse_text_columns(tmp_path):
    # `weekly` is rendered whole (only play_by_play is column-trimmed).
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        conn.execute("CREATE TABLE weekly (position TEXT, note TEXT)")
        rows = [("QB", None)] * 4000 + [
            ("RB", name) for name in ("A.Smith", "B.Jones", "C.Lee")
        ]
        conn.executemany("INSERT INTO weekly VALUES (?,?)", rows)
        conn.commit()
        snapshot = render_schema_snapshot(conn)
    finally:
        conn.close()
    # dense categorical: listed
    assert "position (TEXT) -- values: 'QB', 'RB'" in snapshot
    # 3 non-null rows out of ~4000: below the coverage floor, not listed
    assert "note (TEXT) --" not in snapshot
    assert "  note (TEXT)\n" in snapshot


def test_render_schema_snapshot_skips_high_cardinality_text(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        conn.execute("CREATE TABLE weekly (label TEXT)")
        conn.executemany(
            "INSERT INTO weekly VALUES (?)",
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
    assert counts["weekly"] > 4000  # ~5.6k player-games in a season
    assert 250 < counts["schedules"] < 350  # 272 regular + playoffs
    assert counts["rosters"] > 2500

    conn = connect(db_path)
    # dropped columns really are gone from the stored table
    roster_cols = [r[1] for r in conn.execute("PRAGMA table_info(rosters)")]
    assert "headshot_url" not in roster_cols
    assert "player_id" in roster_cols

    # every curated snapshot column actually exists in play_by_play — a
    # typo in _SNAPSHOT_COLUMNS would otherwise silently drop a column
    pbp_cols = {r[1] for r in conn.execute("PRAGMA table_info(play_by_play)")}
    from nfl_chatdb.ingest import _SNAPSHOT_COLUMNS

    missing = _SNAPSHOT_COLUMNS["play_by_play"] - pbp_cols
    assert not missing, f"snapshot names not in play_by_play: {sorted(missing)}"
    # schedules carries game outcomes at game grain
    close = run_query(
        conn,
        "SELECT COUNT(*) FROM schedules WHERE ABS(result) <= 7",
    )
    assert close.rows[0][0] > 50

    # ingest-time enrichment: seasonal_stats gains a display-name column,
    # and the large majority of rows resolve to a name.
    named = run_query(
        conn,
        "SELECT COUNT(*) FROM seasonal_stats WHERE player_display_name IS NOT NULL",
    )
    assert named.rows[0][0] > 400
    henry = run_query(
        conn,
        "SELECT SUM(rushing_tds) FROM seasonal_stats "
        "WHERE player_display_name = 'Derrick Henry' AND season = 2023",
    )
    assert henry.rows[0][0] and henry.rows[0][0] > 0
