import sqlite3

import pandas as pd
import pytest

from nfl_chatdb.ingest import (
    SEASONS,
    TABLES,
    render_schema_snapshot,
    write_dataframe,
)


def test_seasons_are_last_five_completed():
    assert SEASONS == [2021, 2022, 2023, 2024, 2025]


def test_tables_constant():
    assert TABLES == ("play_by_play", "seasonal_stats", "snap_counts")


def test_write_dataframe_replaces_and_counts(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    n = write_dataframe(df, "play_by_play", conn)
    assert n == 3
    # replace semantics: writing again does not append
    n2 = write_dataframe(df, "play_by_play", conn)
    assert n2 == 3
    assert conn.execute("SELECT COUNT(*) FROM play_by_play").fetchone()[0] == 3


def test_render_schema_snapshot_format(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(
        """
        CREATE TABLE play_by_play (play_id INTEGER, game_id TEXT);
        CREATE TABLE seasonal_stats (player_id TEXT, rushing_tds INTEGER);
        CREATE TABLE snap_counts (pfr_player_id TEXT, offense_snaps INTEGER);
        """
    )
    snapshot = render_schema_snapshot(conn)
    assert "Table: play_by_play" in snapshot
    assert "  play_id (INTEGER)" in snapshot
    assert "  game_id (TEXT)" in snapshot
    assert "Table: seasonal_stats" in snapshot
    assert "Table: snap_counts" in snapshot
    # blocks separated by a blank line
    assert "\n\nTable: seasonal_stats" in snapshot


@pytest.mark.live
def test_ingest_end_to_end(tmp_path):
    """Real pull from nflverse for a single season — slow, network."""
    from nfl_chatdb.ingest import ingest

    counts = ingest(db_path=tmp_path / "nfl.db", seasons=[2023])
    assert counts["play_by_play"] > 40000  # ~48k plays in a season
    assert counts["seasonal_stats"] > 500
    assert counts["snap_counts"] > 5000
