"""text/naming.py: playlist, topic and description names."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.text.naming import canonical_concert_name, playlist_name, teaser_description, topic_name

TZ = ZoneInfo("America/Los_Angeles")


def test_playlist_name_is_date_pipe_artist() -> None:
    assert playlist_name("2026-11-14", "Zed Fox") == "2026-11-14 | Zed Fox"


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("2026/11/14 - X", "2026-11-14 | X"),
        ("2026-11-14 | X", "2026-11-14 | X"),
        ("2026-11-14 X", "2026-11-14 | X"),
        ("  2026/11/14 | Zed Fox  ", "2026-11-14 | Zed Fox"),
        ("X", None),
        ("", None),
        (None, None),
        ("11/14/2026 - X", None),
    ],
)
def test_canonical_concert_name(name: str | None, want: str | None) -> None:
    assert canonical_concert_name(name) == want


def test_topic_name_uses_first_comma_segment() -> None:
    start = datetime(2026, 11, 14, 20, 30, tzinfo=TZ)
    assert topic_name(start, "Zed Fox", "The Quarry, San Francisco, CA") == "2026-11-14 | Zed Fox | The Quarry"


def test_topic_name_without_comma_keeps_whole_location() -> None:
    start = datetime(2026, 11, 14, 20, 30, tzinfo=TZ)
    assert topic_name(start, "Zed Fox", "  The Quarry ") == "2026-11-14 | Zed Fox | The Quarry"


def test_teaser_description() -> None:
    assert (
        teaser_description("Zed Fox", "2026-11-14")
        == "Teaser for Zed Fox on 2026-11-14: every liked song by them, in the order they were liked."
    )
