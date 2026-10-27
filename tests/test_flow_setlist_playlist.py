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
    find_setlist_playlist,
    find_teaser_playlist,
    setlist_playlist_outcome,
    sync_setlist_playlist,
    track_query,
)
from concert_mcp.flows.teaser import make_teaser
from concert_mcp.flows.topics import make_topic_entry, topic_remember
from concert_mcp.result import Kind
from concert_mcp.text.naming import is_setlist_playlist, setlist_description, teaser_description, with_setlist_link
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


def test_track_query_uses_the_cover_artist() -> None:
    assert track_query(Song("First Light"), ARTIST) == "track:First Light artist:Zed Fox"
    assert track_query(Song("Borrowed Song", "Other Band"), ARTIST) == "track:Borrowed Song artist:Other Band"


def test_marker_tells_the_setlist_playlist_from_the_teaser() -> None:
    setlist = {"name": NAME, "description": setlist_description(ARTIST, DATE, PAGE_URL)}
    teaser = {"name": NAME, "description": teaser_description(ARTIST, DATE)}
    assert setlist["description"] == f"Setlist: Zed Fox on 2026-11-14, in setlist order, from {PAGE_URL}"
    assert setlist_description(ARTIST, DATE, PAGE_URL, "https://open.spotify.com/playlist/pl1") == (
        f"Setlist: Zed Fox on 2026-11-14, in setlist order, from {PAGE_URL} "
        "Teaser playlist: https://open.spotify.com/playlist/pl1"
    )
    assert is_setlist_playlist(setlist) and not is_setlist_playlist(teaser)
    assert find_setlist_playlist([teaser, setlist], NAME) is setlist
    assert find_teaser_playlist([setlist, teaser], NAME) is teaser
    assert find_setlist_playlist([teaser], NAME) is None


def test_with_setlist_link_appends_once() -> None:
    desc = teaser_description(ARTIST, DATE)
    once = with_setlist_link(desc, "https://open.spotify.com/playlist/pl2")
    assert once == desc + " Setlist playlist: https://open.spotify.com/playlist/pl2"
    assert with_setlist_link(once, "https://open.spotify.com/playlist/pl9") == once
    assert with_setlist_link(None, "u") == "Setlist playlist: u"


# ---------- sync ----------


def test_sync_creates_the_playlist_in_setlist_order_and_skips_tape_and_misses(svc_cli: Services) -> None:
    wire(svc_cli)
    res = sync_setlist_playlist(artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli)
    sp = svc_cli.spotify
    assert res.status == "created" and res.name == NAME and res.url == "https://open.spotify.com/playlist/pl1"
    assert (res.total, res.added, res.already_there, res.missing) == (5, 3, 0, ("Unreleased Jam",))
    assert res.teaser_url is None
    assert sp.added == [("pl1", ["spotify:track:t1", "spotify:track:c1", "spotify:track:t3"])]
    assert sp.created[0]["description"] == setlist_description(ARTIST, DATE, PAGE_URL)
    assert sp.created[0]["public"] is False
    queries = [a["q"] for m, a in sp.calls if m == "search_tracks"]
    assert queries == [
        "track:First Light artist:Zed Fox",
        "track:Borrowed Song artist:Other Band",
        "track:Third Rail artist:Zed Fox",
        "track:Unreleased Jam artist:Zed Fox",
    ]


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


def test_teaser_does_not_top_up_the_setlist_playlist_of_the_same_name(svc_cli: Services) -> None:
    wire(svc_cli)
    sync_setlist_playlist(artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli)
    res = make_teaser(ARTIST, DATE, svc_cli)
    assert res.status == "created" and res.id == "pl2"
    assert svc_cli.spotify.items["pl1"] and all(r["uri"] != "spotify:track:t2" for r in svc_cli.spotify.items["pl1"])


def test_sync_dry_run_and_unavailable_touch_nothing(svc_cli: Services) -> None:
    wire(svc_cli)
    res = sync_setlist_playlist(
        artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli, dry_run=True
    )
    assert (res.status, res.name, res.total) == ("dry_run", NAME, 5)
    assert svc_cli.spotify.calls == []
    svc_cli.spotify = None
    res = sync_setlist_playlist(artist=ARTIST, date=DATE, songs=[], setlistfm_url=PAGE_URL, svc=svc_cli)
    assert res.status == "unavailable" and res.reason == "Spotify is not set up"


def test_sync_reports_a_spotify_failure(svc_cli: Services) -> None:
    wire(svc_cli)
    svc_cli.spotify.fail = "create_playlist"
    res = sync_setlist_playlist(artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli)
    assert res.status == "failed"
    assert res.reason == "Spotify API refused | method='create_playlist' status='500'"


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


def test_outcome_with_no_date_anywhere_uses_the_page_date_and_says_no_event(svc_cli: Services) -> None:
    wire(svc_cli, songs=[{"name": "First Light"}])
    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli)
    assert out.kind is Kind.OK
    assert out.reply == (
        "**Setlist playlist** [2026-11-12 | Zed Fox](https://open.spotify.com/playlist/pl1): 1 song.\n"
        "No calendar event found for this show; the link was not added to one."
    )


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


def test_outcome_dry_run_calls_no_spotify(svc_cli: Services) -> None:
    wire(svc_cli)
    out = setlist_playlist_outcome(PAGE_URL, "", DATE, "", svc_cli, dry_run=True)
    assert out.kind is Kind.DRY_RUN
    assert out.reply == f"**Setlist playlist** would make 2026-11-14 | Zed Fox from the 5 songs at {PAGE_URL}"
    assert svc_cli.spotify.calls == [] and svc_cli.calendar.calls == []
    svc_cli.setlistfm = None
    out = setlist_playlist_outcome(PAGE_URL, "", DATE, "", svc_cli, dry_run=True)
    assert out.kind is Kind.DRY_RUN
    assert out.reply == f"**Setlist playlist** would make 2026-11-14 | Zed Fox from the songs at {PAGE_URL}"
