"""text/naming.py: playlist, topic and description names."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from concert_mcp.text.naming import canonical_concert_name, playlist_name, teaser_description, topic_name

TZ = ZoneInfo("America/Los_Angeles")


def test_naming() -> None:
    # playlist_name_is_date_pipe_artist:
    assert playlist_name("2026-11-14", "Zed Fox") == "2026-11-14 | Zed Fox"

    # canonical_concert_name:
    for name, want in [
        ("2026/11/14 - X", "2026-11-14 | X"),
        ("2026-11-14 | X", "2026-11-14 | X"),
        ("2026-11-14 X", "2026-11-14 | X"),
        ("  2026/11/14 | Zed Fox  ", "2026-11-14 | Zed Fox"),
        ("X", None),
        ("", None),
        (None, None),
        ("11/14/2026 - X", None),
    ]:
        assert canonical_concert_name(name) == want

    # topic_name_uses_first_comma_segment:
    start = datetime(2026, 11, 14, 20, 30, tzinfo=TZ)
    assert topic_name(start, "Zed Fox", "The Quarry, San Francisco, CA") == "2026-11-14 | Zed Fox | The Quarry"

    # topic_name_without_comma_keeps_whole_location:
    start = datetime(2026, 11, 14, 20, 30, tzinfo=TZ)
    assert topic_name(start, "Zed Fox", "  The Quarry ") == "2026-11-14 | Zed Fox | The Quarry"

    # teaser_description:
    assert teaser_description("Zed Fox", "Ari") == "Ari's liked songs from Zed Fox · Created by AriSweedler/concert-mcp"
