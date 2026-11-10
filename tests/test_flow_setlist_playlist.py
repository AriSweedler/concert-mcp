"""flows/setlist_playlist.py on the fakes: songs_of, the setlist/teaser marker, the idempotent sync, the replies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist_playlist import (
    EVENT_HAD_LINK,
    EVENT_PATCHED,
    NO_PAGE_FIX,
    NO_SETLIST_REPLY,
    NO_SONGS_FIX,
    NOT_SETLISTFM_FIX,
    setlist_playlist_outcome,
    sync_setlist_playlist,
)
from concert_mcp.flows.teaser import make_teaser
from concert_mcp.flows.topics import make_topic_entry, topic_remember
from concert_mcp.result import Kind
from concert_mcp.text.naming import setlist_description, teaser_description, with_setlist_link
from concert_mcp.text.routes import chat_target, forum_target
from concert_mcp.text.setlists import Song, page_artist_of, setlist_id_of, songs_of
from concert_mcp.text.topics import topic_key
from fakes import FakeSetlistFm

ARTIST = "Zed Fox"
DATE = "2026-11-14"
NAME = "2026-11-14 | Zed Fox"
PAGE_ID = "3bd6f4a"
PAGE_URL = f"https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-san-francisco-ca-{PAGE_ID}.html"
TZ = timezone(timedelta(hours=-7))


def page(songs: list[dict[str, Any]] | None = None, event_date: str = "12-11-2026") -> dict[str, Any]:
    """A setlist.fm setlist (another night of the tour by default) as GET /setlist/<id> returns it."""
    if songs is None:
        songs = [
            {"name": "Intro", "tape": True},
            {"name": "First Light"},
            {"name": "Borrowed Song", "cover": {"name": "Other Band"}},
            {"name": "Third Rail"},
            {"name": "Unreleased Jam"},
        ]
    return {
        "id": PAGE_ID,
        "url": PAGE_URL,
        "eventDate": event_date,
        "artist": {"name": ARTIST},
        "venue": {"name": "The Quarry", "city": {"name": "San Francisco"}},
        "sets": {"set": [{"song": songs[:2]}, {"encore": 1, "song": songs[2:]}]},
    }


TRACKS = [
    {"name": "First Light", "artists": ["Zed Fox"], "uri": "spotify:track:t1"},
    {"name": "Third Rail", "artists": ["Zed Fox"], "uri": "spotify:track:t3"},
    {"name": "Borrowed Song", "artists": ["Other Band"], "uri": "spotify:track:c1"},
]


def wire(svc: Services, *, songs: list[dict[str, Any]] | None = None) -> None:
    svc.spotify.tracks = list(TRACKS)  # type: ignore[attr-defined]
    svc.setlistfm = FakeSetlistFm(setlists={PAGE_ID: page(songs)})


# ---------- pure ----------


def test_songs_of_keeps_order_covers_and_tape() -> None:
    assert songs_of(page()) == [
        Song("Intro", None, True),
        Song("First Light"),
        Song("Borrowed Song", "Other Band"),
        Song("Third Rail"),
        Song("Unreleased Jam"),
    ]
    assert songs_of({"sets": {"set": [{"song": [{"name": ""}, {}]}]}}) == []
    assert songs_of(None) == []


def test_setlist_id_of_accepts_only_setlist_pages() -> None:
    assert setlist_id_of(PAGE_URL) == PAGE_ID
    assert setlist_id_of(f" {PAGE_URL}?utm=x ") == PAGE_ID
    assert setlist_id_of("http://setlist.fm/setlist/zed-fox/2026/the-quarry-1a2b3c4d.html") == "1a2b3c4d"
    assert setlist_id_of("https://www.setlist.fm/search?query=Zed%20Fox") is None
    assert setlist_id_of("https://open.spotify.com/playlist/pl1") is None
    assert setlist_id_of("") is None
    assert page_artist_of(PAGE_URL) == "Zed Fox"
    assert page_artist_of("https://www.setlist.fm/search?query=Zed%20Fox") is None


def test_with_setlist_link_appends_once() -> None:
    desc = teaser_description(ARTIST, DATE)
    once = with_setlist_link(desc, "https://open.spotify.com/playlist/pl2")
    assert once == desc + " Setlist playlist: https://open.spotify.com/playlist/pl2"
    assert with_setlist_link(once, "https://open.spotify.com/playlist/pl9") == once
    assert with_setlist_link(None, "u") == "Setlist playlist: u"


# ---------- sync ----------


# ---------- the search fallback (beads-9pb) ----------


def searched(sp: Any) -> list[str]:
    return [a["q"] for m, a in sp.calls if m == "search_tracks"]


def sync_one(svc: Services, song: Song, artist: str = ARTIST) -> tuple[Any, Any]:
    res = sync_setlist_playlist(artist=artist, date=DATE, songs=[song], setlistfm_url=PAGE_URL, svc=svc)
    return res, svc.spotify


def test_the_artist_id_matches_a_track_credited_under_another_spelling(svc_cli: Services) -> None:
    svc_cli.spotify.tracks = [
        {"name": "Alias Song", "artists": ["Z. Fox"], "artist_ids": ["artist-zedfox"], "uri": "spotify:track:a1"}
    ]
    res, sp = sync_one(svc_cli, Song("Alias Song"))
    assert (res.added, res.missing) == (1, ())
    assert sp.added == [("pl1", ["spotify:track:a1"])]
    # Without the id (no exact artist on Spotify), the same track is not the song by this artist.
    svc_cli.spotify.artists = []
    svc_cli.spotify.added.clear()
    svc_cli.spotify.playlists.clear()
    res, sp = sync_one(svc_cli, Song("Alias Song"))
    assert res.missing == ("Alias Song",) and sp.added == []


def test_a_corrected_artist_name_lends_no_id(svc_cli: Services) -> None:
    # `Zed Fox & Friends` resolves to Zed Fox only by word subset: that id must not vouch for tracks.
    svc_cli.spotify.tracks = [
        {"name": "Alias Song", "artists": ["Z. Fox"], "artist_ids": ["artist-zedfox"], "uri": "spotify:track:a1"}
    ]
    res, sp = sync_one(svc_cli, Song("Alias Song"), artist="Zed Fox & Friends")
    assert res.missing == ("Alias Song",) and sp.added == []


def test_debug_log_names_the_query_and_the_candidates(svc_cli: Services, capsys: Any) -> None:
    from concert_mcp.log import set_level, strip_ansi

    svc_cli.spotify.tracks = [{"name": "Hell", "artists": ["Other Band"], "uri": "spotify:track:wrong"}]
    set_level("debug")
    try:
        sync_one(svc_cli, Song("Hell"))
    finally:
        set_level("info")
    err = strip_ansi(capsys.readouterr().err)
    assert (
        "[DEBUG] setlist song search | song='Hell' query='track:Hell artist:Zed Fox' candidates='none' picked='None'"
        in err
    )
    assert (
        "[DEBUG] setlist song search | song='Hell' query='Hell Zed Fox' candidates='Hell by Other Band' picked='None'"
        in err
    )


# ---------- the version the artist originally released (beads-3ly) ----------

ALBUM = {"name": "First Record", "album_type": "album", "release_date": "2017-04-07"}
SESSIONS = {"name": "Harbor Sessions", "album_type": "single", "release_date": "2018-11-16"}
THIRD_RAIL_SESSIONS = {
    "name": "Third Rail - Harbor Sessions",
    "artists": ["Zed Fox"],
    "uri": "spotify:track:t3-sessions",
    "album": SESSIONS,
}
THIRD_RAIL_ALBUM = {"name": "Third Rail", "artists": ["Zed Fox"], "uri": "spotify:track:t3", "album": ALBUM}


def stale_playlist(svc: Services) -> dict[str, Any]:
    """An existing setlist playlist an earlier run filled with the session take of Third Rail, in setlist order."""
    sp = svc.spotify
    sp.tracks = [*TRACKS[:1], THIRD_RAIL_SESSIONS, THIRD_RAIL_ALBUM, *TRACKS[2:]]
    pl = sp.create_playlist(name=NAME, description=setlist_description(ARTIST, DATE, PAGE_URL), public=False)
    sp.items[pl["id"]] = [
        {"name": "First Light", "artists": ["Zed Fox"], "uri": "spotify:track:t1"},
        {"name": "Third Rail - Harbor Sessions", "artists": ["Zed Fox"], "uri": "spotify:track:t3-sessions"},
    ]
    sp.calls.clear()
    return pl


def test_the_reply_names_the_swapped_songs(svc_cli: Services) -> None:
    stale_playlist(svc_cli)
    svc_cli.setlistfm = FakeSetlistFm(setlists={PAGE_ID: page()})
    out = setlist_playlist_outcome(PAGE_URL, "", DATE, "", svc_cli)
    assert out.kind is Kind.OK
    assert out.reply == (
        "**Setlist playlist** [2026-11-14 | Zed Fox](https://open.spotify.com/playlist/pl1): 3 songs, "
        "1 not on Spotify (already existed, 1 added).\n"
        "1 swapped for the original version: Third Rail\n"
        "Not on Spotify: Unreleased Jam\n"
        "No calendar event found for this show; the link was not added to one."
    )
    assert out.data["swapped"] == ["Third Rail"]


def test_sync_tops_up_an_existing_setlist_playlist_and_cross_links_the_teaser(svc_cli: Services) -> None:
    wire(svc_cli)
    teaser = make_teaser(ARTIST, DATE, svc_cli)
    assert teaser.status == "created" and teaser.url == "https://open.spotify.com/playlist/pl1"
    sp = svc_cli.spotify
    existing = sp.create_playlist(name=NAME, description=setlist_description(ARTIST, DATE, PAGE_URL), public=False)
    sp.add_items(existing["id"], ["spotify:track:t1"])
    sp.calls.clear()
    sp.added.clear()

    res = sync_setlist_playlist(artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli)
    assert res.status == "updated" and res.id == "pl2"
    assert (res.added, res.already_there, res.missing) == (2, 1, ("Unreleased Jam",))
    assert res.teaser_url == "https://open.spotify.com/playlist/pl1"
    assert sp.added == [("pl2", ["spotify:track:c1", "spotify:track:t3"])]
    assert sp.described == [
        ("pl1", teaser_description(ARTIST, DATE) + " Setlist playlist: https://open.spotify.com/playlist/pl2"),
        ("pl2", setlist_description(ARTIST, DATE, PAGE_URL, "https://open.spotify.com/playlist/pl1")),
    ]
    assert not [c for c in sp.calls if c[0] == "create_playlist"]

    # A third run changes nothing.
    sp.calls.clear()
    again = sync_setlist_playlist(artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli)
    assert (again.status, again.added, again.already_there) == ("updated", 0, 3)
    assert not [c for c in sp.calls if c[0] in ("add_items", "update_playlist_details", "create_playlist")]


# ---------- the tool ----------


def test_outcome_from_a_pasted_link_in_a_topic_takes_the_topic_event_date(svc: Services) -> None:
    wire(svc)
    start = datetime(2026, 11, 14, 20, 30, tzinfo=TZ)
    e = svc.calendar.seed_event(
        "cal-concerts", summary=ARTIST, start=start, description="Teaser playlist: https://open.spotify.com/playlist/t"
    )
    entry = make_topic_entry(event_id=e["id"], calendar_id="cal-concerts", summary=ARTIST, start=start, now=start)
    chat, thread = forum_target(chat_target(svc.context.route))  # type: ignore[attr-defined,misc]
    topic_remember(topic_key(chat, thread), entry, svc)

    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc)
    assert out.kind is Kind.OK
    assert out.reply == (
        "**Setlist playlist** [2026-11-14 | Zed Fox](https://open.spotify.com/playlist/pl1): 3 songs, "
        "1 not on Spotify.\n"
        "Not on Spotify: Unreleased Jam\n"
        "Calendar event updated with the link."
    )
    assert svc.setlistfm.fetched == [PAGE_ID]
    desc = svc.calendar.events["cal-concerts"][0]["description"]
    assert desc == (
        "Teaser playlist: https://open.spotify.com/playlist/t\nSetlist playlist: https://open.spotify.com/playlist/pl1"
    )
    assert out.data["setlistfm_url"] == PAGE_URL and out.data["event"] == EVENT_PATCHED

    # A repeat call: nothing new, the event keeps one link line.
    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc)
    assert out.reply == (
        "**Setlist playlist** [2026-11-14 | Zed Fox](https://open.spotify.com/playlist/pl1): 3 songs, "
        "1 not on Spotify (already existed, 0 added).\n"
        "Not on Spotify: Unreleased Jam\n"
        "Calendar event already had the link."
    )
    assert svc.calendar.events["cal-concerts"][0]["description"].count("Setlist playlist:") == 1
    assert out.data["event"] == EVENT_HAD_LINK


def test_outcome_without_a_topic_finds_the_event_by_title_and_date(svc_cli: Services) -> None:
    wire(svc_cli)
    svc_cli.calendar.seed_event("cal-concerts", summary="Zed Fox", start=datetime(2026, 11, 14, 20, 30, tzinfo=TZ))
    make_teaser(ARTIST, DATE, svc_cli)
    out = setlist_playlist_outcome(PAGE_URL, "", DATE, "", svc_cli)
    assert out.kind is Kind.OK
    assert out.reply == (
        "**Setlist playlist** [2026-11-14 | Zed Fox](https://open.spotify.com/playlist/pl2): 3 songs, "
        "1 not on Spotify.\n"
        "Not on Spotify: Unreleased Jam\n"
        "Linked with the teaser playlist.\n"
        "Calendar event updated with the link."
    )
    assert svc_cli.calendar.patched[0][2] == {"description": "Setlist playlist: https://open.spotify.com/playlist/pl2"}


def test_outcome_by_artist_and_date_goes_through_find_setlist(svc_cli: Services) -> None:
    wire(svc_cli)
    svc_cli.setlistfm.responses[(ARTIST, "14-11-2026", None)] = {"setlist": [page(event_date="14-11-2026")]}
    out = setlist_playlist_outcome("", ARTIST, DATE, "The Quarry", svc_cli)
    assert out.kind is Kind.OK and out.reply.startswith("**Setlist playlist** [2026-11-14 | Zed Fox]")
    assert svc_cli.setlistfm.fetched == [PAGE_ID]


def test_outcome_fix_lines(svc_cli: Services) -> None:
    wire(svc_cli)
    out = setlist_playlist_outcome("https://open.spotify.com/playlist/pl1", "", "", "", svc_cli)
    assert out.kind is Kind.FIX and out.fix == NOT_SETLISTFM_FIX
    assert out.fix == (
        'send a setlist.fm setlist page, like "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-'
        'san-francisco-ca-3bd6f4a.html", or leave url empty and pass artist and date'
    )
    out = setlist_playlist_outcome(PAGE_URL.replace(PAGE_ID, "0000000"), "", "", "", svc_cli)
    assert out.kind is Kind.FIX and out.fix == NO_PAGE_FIX
    assert out.fix == "that setlist.fm link did not resolve; paste the page's address from the browser"
    wire(svc_cli, songs=[])
    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli)
    assert out.kind is Kind.FIX and out.fix == NO_SONGS_FIX
    assert out.fix == (
        "that setlist.fm page has no songs yet; paste a filled-out page of the same tour, or try after the show"
    )


def test_outcome_needs_artist_and_date_without_a_link(svc_cli: Services) -> None:
    out = setlist_playlist_outcome("", "", "", "", svc_cli)
    assert out.kind is Kind.NEED_USER
    assert out.reply == "I could not read the artist from that. Reply with it and I will add the event."
    out = setlist_playlist_outcome("", ARTIST, "", "", svc_cli)
    assert out.reply == "I could not read the date from that. Reply with it and I will add the event."


def test_outcome_no_page_yet_and_providers_missing(svc_cli: Services) -> None:
    wire(svc_cli)
    out = setlist_playlist_outcome("", ARTIST, DATE, "", svc_cli)
    assert out.kind is Kind.NEED_USER
    assert out.reply == NO_SETLIST_REPLY.format(artist=ARTIST, date=DATE)
    assert out.reply == (
        "No setlist.fm page for Zed Fox on 2026-11-14 yet. Paste one when it appears and I will build the playlist."
    )
    svc_cli.setlistfm = None
    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli)
    assert out.kind is Kind.ERROR
    assert out.reply == "setlist.fm is not set up on this machine. Admin: run `concert-mcp spotify setlist-auth`."
    wire(svc_cli)
    svc_cli.spotify = None
    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli)
    assert out.kind is Kind.ERROR
    assert out.reply == "Spotify is not set up on this machine. Admin: run `concert-mcp spotify auth`."


def test_outcome_spotify_failure_is_a_reply(svc_cli: Services) -> None:
    wire(svc_cli)
    svc_cli.spotify.fail = "my_playlists"
    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli)
    assert out.kind is Kind.ERROR
    assert out.reply == "**Setlist playlist** not made (Spotify API refused | method='my_playlists' status='500')"


def test_outcome_with_no_date_anywhere_uses_the_page_date_and_says_no_event(svc_cli: Services) -> None:
    wire(svc_cli, songs=[{"name": "First Light"}])
    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli)
    assert out.kind is Kind.OK
    assert out.reply == (
        "**Setlist playlist** [2026-11-12 | Zed Fox](https://open.spotify.com/playlist/pl1): 1 song.\n"
        "No calendar event found for this show; the link was not added to one."
    )
