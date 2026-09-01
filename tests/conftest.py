"""Shared test fixtures."""

from __future__ import annotations

import sqlite3

import pytest

# A tiny schema snapshot string used by Stage 1 / Stage 2 / pipeline tests
# so they do not depend on a real ingested database.
FAKE_SCHEMA_TEXT = """\
Table: play_by_play
  play_id (INTEGER)
  game_id (TEXT)
  season (INTEGER)
  week (INTEGER)
  posteam (TEXT)
  rusher_player_name (TEXT)
  rush_touchdown (INTEGER)
  yards_gained (INTEGER)

Table: seasonal_stats
  player_id (TEXT)
  player_display_name (TEXT)
  season (INTEGER)
  rushing_tds (INTEGER)
  rushing_yards (INTEGER)

Table: snap_counts
  pfr_player_id (TEXT)
  player (TEXT)
  season (INTEGER)
  week (INTEGER)
  offense_snaps (INTEGER)
"""


@pytest.fixture
def fake_schema_text() -> str:
    return FAKE_SCHEMA_TEXT


@pytest.fixture
def tiny_db(tmp_path):
    """A minimal on-disk SQLite db matching FAKE_SCHEMA_TEXT's play_by_play."""
    path = tmp_path / "tiny.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE play_by_play (
            play_id INTEGER, game_id TEXT, season INTEGER, week INTEGER,
            posteam TEXT, rusher_player_name TEXT, rush_touchdown INTEGER,
            yards_gained INTEGER
        );
        INSERT INTO play_by_play VALUES
            (1, '2023_01_A_B', 2023, 1, 'TEN', 'D.Henry', 1, 12),
            (2, '2023_01_A_B', 2023, 1, 'TEN', 'D.Henry', 0, 4),
            (3, '2023_02_A_C', 2023, 2, 'TEN', 'D.Henry', 1, 3);
        """
    )
    conn.commit()
    conn.close()
    return path
