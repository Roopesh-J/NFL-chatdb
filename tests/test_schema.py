import pytest

from nfl_chatdb.schema import load_schema_text


def test_load_schema_text_reads_committed_snapshot():
    text = load_schema_text()
    assert "Table: play_by_play" in text
    assert "Table: seasonal_stats" in text
    assert "Table: snap_counts" in text


def test_load_schema_text_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError) as excinfo:
        load_schema_text(tmp_path / "nope.txt")
    assert "nfl_chatdb.ingest" in str(excinfo.value)
