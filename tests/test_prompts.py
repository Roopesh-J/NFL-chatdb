from nfl_chatdb.prompts import cached_schema_system


def test_cached_schema_block_is_marked_and_holds_the_schema():
    block = cached_schema_system("Table: weekly\n  player_id (TEXT)")
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "player_id (TEXT)" in block["text"]
