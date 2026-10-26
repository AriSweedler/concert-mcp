"""text/reply.py: every string a human reads, byte for byte."""

from __future__ import annotations

from pathlib import Path

import pytest

from concert_mcp.flows.queue import QueueEntry, QueueStatus
from concert_mcp.result import NO_REPLY_TEXT
from concert_mcp.text import reply
from concert_mcp.text.artists import Artist, TeaserResult
from concert_mcp.text.candidates import Candidate
from concert_mcp.text.reply import (
    LINK_LINE_PREFIXES,
    NO_CANDIDATES_REPLY,
    WHICH_EVENT_REPLY,
    bullet,
    bullets,
    dropped_note,
    event_link,
    provider_reply,
    render_added,
    render_body,
    render_candidates,
    render_dry_add,
    render_none_found,
    render_queue,
    render_removed,
    render_setlist_standalone,
    render_teaser_standalone,
    render_would_look,
    setlist_bullet,
    setlist_line,
    teaser_bullet,
    teaser_line,
)
from concert_mcp.text.setlists import Setlist, SetlistResult

ZED = Artist(name="Zed Fox", id="artist-zedfox", url="https://open.spotify.com/artist/artist-zedfox")
TEASER_URL = "https://open.spotify.com/playlist/pl1"
SETLIST_URL = "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-san-francisco-ca-1.html"
WHEN = "Sat Nov 14, 8:30 PM – 10:30 PM PST"


def teaser(status: str, **kw: object) -> TeaserResult:
    base: dict[str, object] = {
        "status": status,
        "artist": ZED,
        "date": "2026-11-14",
        "name": "2026-11-14 | Zed Fox",
        "liked_count": 3,
    }
    base.update(kw)
    return TeaserResult(**base)  # type: ignore[arg-type]


def setlist(song_count: int) -> Setlist:
    songs = tuple(f"Song {i}" for i in range(song_count))
    return Setlist(
        id="sl1",
        url=SETLIST_URL,
        artist="Zed Fox",
        date="2026-11-14",
        venue="The Quarry",
        city="San Francisco",
        tour=None,
        song_count=song_count,
        songs=songs,
    )


def candidate(location: str = "The Quarry, San Francisco", description: str = "") -> Candidate:
    return Candidate(
        id="ev1",
        calendar_id="cal-concerts",
        calendar="Concerts",
        summary="Zed Fox",
        when=WHEN,
        start="2026-11-14T20:30:00-08:00",
        location=location,
        html_link="https://www.google.com/calendar/event?eid=abc123",
        description=description,
    )


def entry(date: str, artist: str, venue: str, song_count: int, until: str) -> QueueEntry:
    return QueueEntry(
        event_id=f"ev-{date}",
        calendar_id="cal-concerts",
        artist=artist,
        date=date,
        venue=venue,
        setlistfm_url=None,
        song_count=song_count,
        until=until,
        added="2026-10-25T19:00:00+00:00",
    )


# ---------- bullets ----------


def test_bullet_is_bold_key_plain_value() -> None:
    assert bullet("Doors", "7:30 PM") == "• **Doors:** 7:30 PM"


def test_bullets_key_rule() -> None:
    long_key = "x" * 25 + ": value"
    out = bullets("Zed Fox", WHEN, "The Quarry", ["Doors: 7:30 PM", "x" * 24 + ": ok", long_key, "no key here"])
    assert out.split("\n") == [
        "• **Artist:** Zed Fox",
        f"• **When:** {WHEN}",
        "• **Where:** The Quarry",
        "• **Doors:** 7:30 PM",
        f"• **{'x' * 24}:** ok",
        f"• {long_key}",
        "• no key here",
    ]


def test_bullets_without_space_after_colon_is_plain() -> None:
    assert bullets("A", "B", "C", ["http://x:y"]).split("\n")[-1] == "• http://x:y"


# ---------- the calendar link ----------


def test_event_link_is_eventedit_form() -> None:
    # Four link forms were tested on a phone on 2026-10-25 against a real event: the API's htmlLink
    # (www.google.com/calendar/event?eid=), calendar.google.com/calendar/event?eid=, the day view and the share
    # template all opened the Google Calendar app but not the event. Only the eventedit form opened the event.
    # It is therefore the only form this package may emit; no other form and no fallback form is allowed.
    assert (
        event_link("https://www.google.com/calendar/event?eid=abc123")
        == "https://calendar.google.com/calendar/u/0/r/eventedit/abc123"
    )
    assert (
        event_link("https://www.google.com/calendar/event?eid=abc123&ctz=America/Los_Angeles")
        == "https://calendar.google.com/calendar/u/0/r/eventedit/abc123"
    )
    assert event_link("https://example.invalid/x") == "https://example.invalid/x"
    assert event_link(None) is None
    assert event_link("") == ""


def test_no_other_calendar_link_form_is_emitted() -> None:
    src = Path(reply.__file__).read_text(encoding="utf-8")
    assert "calendar.google.com/calendar/u/0/r/eventedit/" in src
    for forbidden in ("calendar/event?eid", "calendar/r/day", "calendar/render", "calendar/u/0/r/day"):
        assert forbidden not in src, forbidden


# ---------- add replies ----------

BODY = "• **Artist:** Zed Fox\n• **When:** when\n• **Where:** where"


def test_render_added_new() -> None:
    out = render_added(
        calendar="Concerts",
        body=BODY,
        notes=["Start corrected to 8:30 PM, the show time in the text."],
        html_link="https://www.google.com/calendar/event?eid=abc123",
        duplicate=False,
    )
    assert out == (
        "**Added to Concerts**\n"
        f"{BODY}\n"
        "Start corrected to 8:30 PM, the show time in the text.\n"
        "[Open in Google Calendar](https://calendar.google.com/calendar/u/0/r/eventedit/abc123)"
    )


def test_render_added_duplicate() -> None:
    out = render_added(
        calendar="Concerts",
        body=BODY,
        notes=[],
        html_link="https://www.google.com/calendar/event?eid=abc123",
        duplicate=True,
    )
    assert out == (
        "**Already on Concerts** (nothing new created)\n"
        f"{BODY}\n"
        "[Open in Google Calendar](https://calendar.google.com/calendar/u/0/r/eventedit/abc123)"
    )


def test_render_dry_add() -> None:
    assert render_dry_add("Personal", BODY, []) == f"**Would add to Personal**\n{BODY}"
    assert render_dry_add("Personal", BODY, ["a", "b"]) == f"**Would add to Personal**\n{BODY}\na\nb"


def test_dropped_note() -> None:
    assert (
        dropped_note(["Price: $99", "Doors: 6 PM"]) == "Left out (not in the text I was given): Price: $99; Doors: 6 PM"
    )


# ---------- teaser ----------


def test_teaser_bullet_none_when_not_wanted() -> None:
    assert teaser_bullet(None, timeout_s=150, dry=False) is None
    assert teaser_line(None) is None


def test_teaser_bullet_every_status() -> None:
    kw = {"timeout_s": 150, "dry": False}
    assert teaser_bullet(teaser("unavailable", artist=None, name=None, liked_count=0), **kw) == (
        "• **Teaser:** not made (Spotify is not set up: run `concert-mcp spotify auth`)"
    )
    assert teaser_bullet(teaser("timeout", liked_count=0), **kw) == (
        "• **Teaser:** not made (Spotify took longer than 150 s)"
    )
    assert teaser_bullet(teaser("failed", reason="no Spotify artist matches | artist='ZZZ'"), **kw) == (
        "• **Teaser:** not made (no Spotify artist matches | artist='ZZZ')"
    )
    assert teaser_bullet(teaser("failed", reason=None), **kw) == "• **Teaser:** not made (unknown error)"
    assert teaser_bullet(teaser("failed", reason="x" * 200), **kw) == f"• **Teaser:** not made ({'x' * 160})"
    assert teaser_bullet(teaser("none", liked_count=0), **kw) == "• **Teaser:** none, no liked songs by Zed Fox yet"
    assert (
        teaser_bullet(teaser("none", artist=None, liked_count=0), **kw) == "• **Teaser:** none, no liked songs by ? yet"
    )
    assert teaser_bullet(teaser("dry_run", exists=True), **kw) == (
        "• **Teaser:** would make 2026-11-14 | Zed Fox (3 liked songs)"
    )
    assert teaser_bullet(teaser("created", url=None), **kw) == (
        "• **Teaser:** would make 2026-11-14 | Zed Fox (3 liked songs)"
    )
    assert teaser_bullet(teaser("created", url=TEASER_URL, id="pl1", added=3), **kw) == (
        f"• **Teaser:** [2026-11-14 | Zed Fox]({TEASER_URL}) 3 liked songs. Move it to Concerts › Teasers in Spotify."
    )
    assert teaser_bullet(teaser("updated", url=TEASER_URL, id="pl1", added=1, already_there=2), **kw) == (
        f"• **Teaser:** [2026-11-14 | Zed Fox]({TEASER_URL}) 3 liked songs (already existed, topped up). "
        "Move it to Concerts › Teasers in Spotify."
    )


def test_teaser_bullet_dry_flag_shows_would_make_even_with_url() -> None:
    assert teaser_bullet(teaser("created", url=TEASER_URL), timeout_s=150, dry=True) == (
        "• **Teaser:** would make 2026-11-14 | Zed Fox (3 liked songs)"
    )


def test_teaser_line_only_for_real_playlists() -> None:
    assert teaser_line(teaser("created", url=TEASER_URL)) == f"Teaser playlist: {TEASER_URL}"
    assert teaser_line(teaser("updated", url=TEASER_URL)) == f"Teaser playlist: {TEASER_URL}"
    assert teaser_line(teaser("created", url=None)) is None
    assert teaser_line(teaser("dry_run")) is None
    assert teaser_line(teaser("none", liked_count=0)) is None
    assert teaser_line(teaser("failed", reason="x")) is None


# ---------- setlist ----------


def test_setlist_bullet_none_when_not_wanted() -> None:
    assert setlist_bullet(None) is None
    assert setlist_line(None) is None


def test_setlist_bullet_every_branch() -> None:
    base = {"artist": "Zed Fox", "date": "2026-11-14", "venue": "The Quarry"}
    assert setlist_bullet(SetlistResult(found=False, match="unavailable", **base)) == (
        "• **Setlist:** lookup skipped (setlist.fm is not set up: run `concert-mcp spotify setlist-auth`)"
    )
    assert setlist_bullet(
        SetlistResult(found=False, match="failed", reason="setlist.fm refused | status='500'", **base)
    ) == ("• **Setlist:** lookup failed (setlist.fm refused | status='500')")
    assert setlist_bullet(SetlistResult(found=False, match="failed", reason=None, **base)) == (
        "• **Setlist:** lookup failed (unknown error)"
    )
    assert setlist_bullet(
        SetlistResult(found=False, match="none", search_url="https://www.setlist.fm/search?query=Zed+Fox", **base)
    ) == ("• **Setlist:** not on setlist.fm yet; checking weekly for 2 months")
    assert setlist_bullet(SetlistResult(found=True, match="exact date", setlist=setlist(12), **base)) == (
        f"• **Setlist:** [setlist.fm]({SETLIST_URL}) 12 songs so far; checking weekly for 2 months"
    )
    assert setlist_bullet(SetlistResult(found=True, match="within a day", setlist=setlist(0), **base)) == (
        f"• **Setlist:** [setlist.fm]({SETLIST_URL}) no songs yet; checking weekly for 2 months"
    )


def test_setlist_line_only_when_found() -> None:
    base = {"artist": "Zed Fox", "date": "2026-11-14", "venue": None}
    assert (
        setlist_line(SetlistResult(found=True, match="exact date", setlist=setlist(1), **base))
        == f"Setlist: {SETLIST_URL}"
    )
    assert setlist_line(SetlistResult(found=False, match="none", **base)) is None


# ---------- body ----------


def test_render_body_strips_link_lines_and_appends_extras() -> None:
    lines = [
        "Doors: 7:30 PM",
        f"Teaser playlist: {TEASER_URL}",
        f"Setlist: {SETLIST_URL}",
        "Headliner: Zed Fox",
        "Opener: Zork.",
    ]
    out = render_body("Zed Fox", WHEN, "The Quarry", lines, ["• **Teaser:** t", None, "• **Setlist:** s"])
    assert out == (
        "• **Artist:** Zed Fox\n"
        f"• **When:** {WHEN}\n"
        "• **Where:** The Quarry\n"
        "• **Doors:** 7:30 PM\n"
        "• **Opener:** Zork.\n"
        "• **Teaser:** t\n"
        "• **Setlist:** s"
    )
    assert LINK_LINE_PREFIXES == ("Teaser playlist: ", "Setlist: ")


# ---------- delete replies ----------


def test_render_removed_without_teaser_line() -> None:
    assert render_removed(candidate(description="Doors: 7:30 PM"), dry=False) == (
        f"**Removed from Concerts**\n• **Artist:** Zed Fox\n• **When:** {WHEN}\n• **Where:** The Quarry, San Francisco"
    )


def test_render_removed_with_teaser_line_and_dry() -> None:
    c = candidate(location="", description=f"Doors: 7:30 PM\nTeaser playlist: {TEASER_URL}\nSetlist: {SETLIST_URL}")
    assert render_removed(c, dry=True) == (
        "**Would remove from Concerts**\n• **Artist:** Zed Fox\n"
        f"• **When:** {WHEN}\n"
        f'• **Teaser:** [playlist]({TEASER_URL}) left in place; say "delete the teaser playlist" to remove it'
    )


def test_render_candidates_numbering_and_optional_location() -> None:
    a = candidate()
    b = Candidate(
        id="ev2",
        calendar_id="primary",
        calendar="Personal",
        summary="Zed Fox afterparty",
        when="Sun Nov 15 (all day)",
        start="2026-11-15T00:00:00-08:00",
        location="",
        html_link=None,
        description="",
    )
    assert render_candidates("zed fox", [a, b]) == (
        "I found 2 events like **zed fox**. Reply with the number to delete:\n"
        f"1. **Zed Fox** on Concerts: {WHEN}, The Quarry, San Francisco\n"
        "2. **Zed Fox afterparty** on Personal: Sun Nov 15 (all day)"
    )


def test_render_none_found() -> None:
    names = ["Concerts", "Personal"]
    assert render_none_found("Zed Fox", None, names) == (
        "I found no event like **Zed Fox** on Concerts or Personal. Nothing deleted."
    )
    assert render_none_found("Zed Fox", "2026-11-14", names) == (
        "I found no event like **Zed Fox** around 2026-11-14 on Concerts or Personal. Nothing deleted."
    )


def test_render_would_look() -> None:
    names = ["Concerts", "Personal"]
    assert render_would_look("Zed Fox", None, names) == (
        "**Would look for** Zed Fox on Concerts and Personal, then remove the one match."
    )
    assert render_would_look("Zed Fox", "2026-11-14", names) == (
        "**Would look for** Zed Fox around 2026-11-14 on Concerts and Personal, then remove the one match."
    )


def test_fixed_replies() -> None:
    assert NO_CANDIDATES_REPLY == (
        "I do not have a list to pick from any more. Tell me the event name and date and I will look again."
    )
    assert (
        WHICH_EVENT_REPLY
        == "Which event? Tell me the artist or title (and the date if you know it) and I will remove it."
    )
    assert reply.NO_REPLY_TEXT is NO_REPLY_TEXT


# ---------- standalone tools ----------


def test_render_teaser_standalone() -> None:
    assert render_teaser_standalone(teaser("created", url=TEASER_URL), timeout_s=150, dry=False) == (
        "**Teaser playlist**\n"
        f"• **Teaser:** [2026-11-14 | Zed Fox]({TEASER_URL}) 3 liked songs. Move it to Concerts › Teasers in Spotify."
    )


def test_render_setlist_standalone() -> None:
    base = {"artist": "Zed Fox", "date": "2026-11-14", "venue": "The Quarry"}
    found = SetlistResult(found=True, match="exact date", setlist=setlist(12), **base)
    assert render_setlist_standalone(found) == (
        f"**Setlist**\n• **Setlist:** [setlist.fm]({SETLIST_URL}) 12 songs so far"
    )
    miss = SetlistResult(found=False, match="none", search_url="https://www.setlist.fm/search?query=Zed+Fox", **base)
    assert render_setlist_standalone(miss) == (
        "**Setlist**\n• **Setlist:** not on setlist.fm yet Search: https://www.setlist.fm/search?query=Zed+Fox"
    )
    unavailable = SetlistResult(
        found=False, match="unavailable", search_url="https://www.setlist.fm/search?query=x", **base
    )
    assert render_setlist_standalone(unavailable) == (
        "**Setlist**\n• **Setlist:** lookup skipped (setlist.fm is not set up: run `concert-mcp spotify setlist-auth`)"
    )


# ---------- queue ----------


def test_render_queue_empty(tmp_path: Path) -> None:
    status = QueueStatus(entries=(), active=(), expired=(), path=tmp_path / "q.json")
    assert render_queue(status) == "The setlist queue is empty."


def test_render_queue_active_then_expired_by_date(tmp_path: Path) -> None:
    late = entry("2026-12-01", "Bonobo", "The Other Hall", 0, "2027-01-31")
    early = entry("2026-11-14", "Zed Fox", "The Quarry", 12, "2027-01-14")
    old = entry("2026-08-01", "Zork.", "Side Room", 9, "2026-10-01")
    status = QueueStatus(entries=(late, old, early), active=(late, early), expired=(old,), path=tmp_path / "q.json")
    assert render_queue(status) == (
        "**Setlist queue** (3 shows)\n"
        "• 2026-11-14 Zed Fox @ The Quarry — 12 songs, checking until 2027-01-14\n"
        "• 2026-12-01 Bonobo @ The Other Hall — 0 songs, checking until 2027-01-31\n"
        "• 2026-08-01 Zork. @ Side Room — 9 songs, checking until 2026-10-01 (expired)"
    )


# ---------- provider replies ----------


@pytest.mark.parametrize(
    ("provider", "kw", "want"),
    [
        ("gws", {}, "Google Calendar refused the request. Admin: check `gws auth` on the host."),
        ("gws", {"what": "delete"}, "Google Calendar refused the delete. Admin: check `gws auth` on the host."),
        ("gws-nojson", {}, "Google Calendar gave an unreadable answer. Admin: check the host."),
        (
            "gws-keychain",
            {},
            "The login keychain is locked in this session, so I did not touch Google Calendar. "
            "Admin: create the gws file keyring key (README.md#google-calendar) "
            "or run this from a session with keychain access.",
        ),
        ("gws-missing", {}, "The calendar tool is not reachable right now. Try again in a minute."),
        ("spotify-unconfigured", {}, "Spotify is not set up on this machine. Admin: run `concert-mcp spotify auth`."),
        (
            "setlistfm-unconfigured",
            {},
            "setlist.fm is not set up on this machine. Admin: run `concert-mcp spotify setlist-auth`.",
        ),
        ("spotify", {}, "Spotify refused the request. Admin: check the host log."),
        ("setlistfm", {}, "setlist.fm refused the request. Admin: check the host log."),
        ("op", {}, "1Password did not answer. Admin: run the auth command with --manual."),
        ("telegram", {}, "telegram failed. Admin: check the host log."),
    ],
)
def test_provider_reply(provider: str, kw: dict[str, str], want: str) -> None:
    assert provider_reply(provider, "Admin", **kw) == want


def test_provider_reply_carries_no_personal_name() -> None:
    for provider in ("gws", "gws-nojson", "gws-keychain", "spotify", "setlistfm", "op"):
        text = provider_reply(provider, "Operator")
        assert "Ari" not in text
        assert "laptop" not in text
