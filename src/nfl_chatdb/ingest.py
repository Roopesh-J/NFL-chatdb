"""One-time ingestion of nflverse datasets into local SQLite.

Also regenerates the committed schema snapshot that Stage 1 / Stage 2
load into their prompts. Re-run whenever the season list changes.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from nfl_chatdb.database import DEFAULT_DB_PATH

SEASONS = [2021, 2022, 2023, 2024, 2025]
TABLES = ("play_by_play", "seasonal_stats", "snap_counts")
SCHEMA_SNAPSHOT_PATH = Path(__file__).parent / "schema_snapshot.txt"


def write_dataframe(df, table: str, conn: sqlite3.Connection) -> int:
    df.to_sql(table, conn, if_exists="replace", index=False)
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def render_schema_snapshot(conn: sqlite3.Connection) -> str:
    blocks: list[str] = []
    for table in TABLES:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        lines = [f"Table: {table}"]
        for _cid, name, col_type, *_rest in rows:
            col_type = col_type or "?"
            lines.append(f"  {name} ({col_type})")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def add_player_display_name(seasonal_df, rosters_df):
    """Left-merge a `player_display_name` column onto seasonal_stats.

    `nfl.import_seasonal_data` returns only `player_id` (GSIS ids like
    `00-0035700`). `nfl.import_seasonal_rosters` carries `player_id` +
    `player_name` per season, so we merge on (season, player_id). Rows with
    no roster match keep a NULL name.
    """
    names = (
        rosters_df[["season", "player_id", "player_name"]]
        .dropna(subset=["player_id"])
        .drop_duplicates(subset=["season", "player_id"])
        .rename(columns={"player_name": "player_display_name"})
    )
    return seasonal_df.merge(names, on=["season", "player_id"], how="left")


def _load_datasets(seasons: list[int]):
    """Return {table_name: DataFrame} pulled from nfl_data_py."""
    import nfl_data_py as nfl

    seasonal = nfl.import_seasonal_data(seasons)
    rosters = nfl.import_seasonal_rosters(seasons)
    seasonal = add_player_display_name(seasonal, rosters)

    return {
        "play_by_play": nfl.import_pbp_data(seasons, downcast=True, cache=False),
        "seasonal_stats": seasonal,
        "snap_counts": nfl.import_snap_counts(seasons),
    }


def ingest(
    db_path: Path = DEFAULT_DB_PATH,
    seasons: list[int] = SEASONS,
    snapshot_path: Path = SCHEMA_SNAPSHOT_PATH,
) -> dict[str, int]:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    datasets = _load_datasets(seasons)
    conn = sqlite3.connect(db_path)
    try:
        counts = {
            table: write_dataframe(datasets[table], table, conn)
            for table in TABLES
        }
        conn.commit()
        Path(snapshot_path).write_text(render_schema_snapshot(conn))
    finally:
        conn.close()
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ingest nflverse data into SQLite")
    parser.add_argument(
        "--seasons",
        type=int,
        nargs="+",
        default=SEASONS,
        help="Seasons to ingest (default: last 5 completed).",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args(argv)
    counts = ingest(db_path=args.db, seasons=args.seasons)
    for table, count in counts.items():
        print(f"{table}: {count:,} rows")
    print(f"schema snapshot written to {SCHEMA_SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
