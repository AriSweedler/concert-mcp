"""flows/setlist_playlist.py on the fakes: songs_of, the setlist/teaser marker, the idempotent sync, the replies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist_playlist import (
    EVENT_HAD_LINK,
    EVENT_PATCHED,
    NO_PAGE_FIX,
    NO_SETLIST_GIVEN_REPLY,
    NO_SETLIST_REPLY,
    NO_SONGS_FIX,
    NO_TOPIC_SHOW_REPLY,
    NOT_SETLISTFM_FIX,
    setlist_playlist_outcome,
    sync_setlist_playlist,
)
from concert_mcp.flows.teaser import make_teaser
from concert_mcp.flows.topics import make_topic_entry, topic_remember
from concert_mcp.result import Kind
from concert_mcp.text.naming import is_setlist_playlist, setlist_description, teaser_description
from concert_mcp.text.routes import chat_target, forum_target
from concert_mcp.text.setlists import Song, page_artist_of, setlist_id_of, songs_of
from concert_mcp.text.topics import topic_key
from fakes import FakePartiful, FakeSetlistFm

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


OLD_SETLIST = f"Setlist: {ARTIST} on {DATE}, in setlist order, from {PAGE_URL}"  # as written before 2026-11-10


def today(svc: Services) -> str:
    return svc.clock.now().astimezone(svc.tz).date().isoformat()


def test_the_descriptions_say_what_the_name_does_not() -> None:
    # One line (Spotify refuses a newline), no link to the other playlist (it cannot be clicked), the repo last.
    assert setlist_description(PAGE_URL, "2026-11-20") == (
        f"Setlist, according to {PAGE_URL} on 2026-11-20 · Created by github.com/AriSweedler/concert-mcp"
    )
    assert (
        teaser_description(ARTIST, "Ari")
        == "Ari's liked songs from Zed Fox · Created by github.com/AriSweedler/concert-mcp"
    )
    # Both playlists of a show share a name; the description's start tells them apart, in either form.
    assert is_setlist_playlist({"description": setlist_description(PAGE_URL, "2026-11-20")})
    assert is_setlist_playlist({"description": OLD_SETLIST})
    assert not is_setlist_playlist({"description": teaser_description(ARTIST, "Ari")})
    assert not is_setlist_playlist({"description": "Teaser for Zed Fox on 2026-11-14: every liked song by them."})
    assert not is_setlist_playlist({})


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
    pl = sp.create_playlist(name=NAME, description=OLD_SETLIST, public=False)
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


def test_sync_tops_up_an_existing_setlist_playlist_and_leaves_its_old_description(svc_cli: Services) -> None:
    wire(svc_cli)
    teaser = make_teaser(ARTIST, DATE, svc_cli)
    assert teaser.status == "created" and teaser.url == "https://open.spotify.com/playlist/pl1"
    sp = svc_cli.spotify
    existing = sp.create_playlist(name=NAME, description=OLD_SETLIST, public=False)
    sp.add_items(existing["id"], ["spotify:track:t1"])
    sp.calls.clear()
    sp.added.clear()

    res = sync_setlist_playlist(artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli)
    assert res.status == "updated" and res.id == "pl2"
    assert (res.added, res.already_there, res.missing) == (2, 1, ("Unreleased Jam",))
    assert res.teaser_url == "https://open.spotify.com/playlist/pl1"
    assert sp.added == [("pl2", ["spotify:track:c1", "spotify:track:t3"])]
    # No cross-links, and a playlist made before the new text keeps the description it was given.
    assert sp.described == []
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
        "Calendar event updated with the links."
    )
    assert svc.setlistfm.fetched == [PAGE_ID]
    desc = svc.calendar.events["cal-concerts"][0]["description"]
    assert desc == (
        "Teaser playlist: https://open.spotify.com/playlist/t\n"
        f"setlist.fm: {PAGE_URL}\n"
        "Setlist playlist: https://open.spotify.com/playlist/pl1"
    )
    assert out.data["setlistfm_url"] == PAGE_URL and out.data["event"] == EVENT_PATCHED

    # A repeat call: nothing new, the event keeps one link line.
    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc)
    assert out.reply == (
        "**Setlist playlist** [2026-11-14 | Zed Fox](https://open.spotify.com/playlist/pl1): 3 songs, "
        "1 not on Spotify (already existed, 0 added).\n"
        "Not on Spotify: Unreleased Jam\n"
        "Calendar event already had the links."
    )
    assert svc.calendar.events["cal-concerts"][0]["description"].count("Setlist playlist:") == 1
    assert out.data["event"] == EVENT_HAD_LINK


def test_a_named_topic_gets_both_links_on_its_event_and_on_its_partiful(svc_cli: Services) -> None:
    # /setlist, typed after the show in its topic: there is no live chat to ask, the command names the topic.
    wire(svc_cli)
    topic = "-1001234567890:7"
    start = datetime(2026, 11, 14, 20, 30, tzinfo=TZ)
    tickets = "https://www.ticketweb.com/event/zed-fox/123"
    e = svc_cli.calendar.seed_event(
        "cal-concerts",
        summary=ARTIST,
        start=start,
        description=f"Tickets: {tickets}\nPartiful: https://partiful.com/e/pf1",
    )
    topic_remember(
        topic,
        make_topic_entry(event_id=e["id"], calendar_id="cal-concerts", summary=ARTIST, start=start, now=start),
        svc_cli,
    )
    partiful = FakePartiful()
    partiful.events.append({"customFields": [{"icon": "link", "value": "Tickets", "url": tickets}]})
    svc_cli.partiful = partiful

    out = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli, topic=f" {topic} ")
    assert out.kind is Kind.OK
    assert out.reply.endswith("\nCalendar event updated with the links. The Partiful has the links too.")
    played = "https://open.spotify.com/playlist/pl1"
    assert svc_cli.calendar.events["cal-concerts"][0]["description"] == (
        f"Tickets: {tickets}\nPartiful: https://partiful.com/e/pf1\nsetlist.fm: {PAGE_URL}\nSetlist playlist: {played}"
    )
    assert partiful.events[0]["customFields"] == [
        {"icon": "link", "value": "Tickets", "url": tickets},
        {"icon": "link", "value": "setlist.fm", "url": PAGE_URL},
        {"icon": "music-note", "value": "Setlist playlist", "url": played},
    ]
    # An event from before 2026-11-14 carries the page as `Setlist:`; it is not linked a second time.
    svc_cli.calendar.events["cal-concerts"][0]["description"] = f"Setlist: {PAGE_URL}"
    again = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli, topic=topic)
    assert again.kind is Kind.OK
    assert (
        svc_cli.calendar.events["cal-concerts"][0]["description"] == f"Setlist: {PAGE_URL}\nSetlist playlist: {played}"
    )


def test_setlist_with_no_link_refreshes_the_page_already_given_or_says_none_was(svc_cli: Services) -> None:
    wire(svc_cli)
    topic = "-1001234567890:7"
    start = datetime(2026, 11, 14, 20, 30, tzinfo=TZ)
    # A topic with no show, then a show with no setlist yet: nothing to refresh, and nothing is searched for.
    none = setlist_playlist_outcome("", "", "", "", svc_cli, topic=topic)
    assert none.kind is Kind.NEED_USER and none.reply == NO_TOPIC_SHOW_REPLY
    e = svc_cli.calendar.seed_event("cal-concerts", summary=ARTIST, start=start, description="Doors: 7:30 PM")
    topic_remember(
        topic,
        make_topic_entry(event_id=e["id"], calendar_id="cal-concerts", summary=ARTIST, start=start, now=start),
        svc_cli,
    )
    asked = setlist_playlist_outcome("", "", "", "", svc_cli, topic=topic)
    assert asked.kind is Kind.NEED_USER and asked.error == "no setlist.fm page given"
    assert asked.reply == "You haven't given this show's setlist yet. Send /setlist <link to its setlist.fm page>."
    assert asked.reply == NO_SETLIST_GIVEN_REPLY and svc_cli.setlistfm.fetched == [] and svc_cli.spotify.created == []

    # Given once, the page is on the event; /setlist alone reads it again and tops the playlist up.
    first = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli, topic=topic)
    assert first.kind is Kind.OK and len(svc_cli.spotify.created) == 1
    again = setlist_playlist_outcome("", "", "", "", svc_cli, topic=topic)
    assert again.kind is Kind.OK and "(already existed, 0 added)" in again.reply
    assert again.reply.endswith("\nCalendar event already had the links.")
    assert svc_cli.setlistfm.fetched == [PAGE_ID, PAGE_ID] and len(svc_cli.spotify.created) == 1
    # The event deleted since: the topic owns nothing to refresh.
    svc_cli.calendar.events["cal-concerts"] = []
    gone = setlist_playlist_outcome("", "", "", "", svc_cli, topic=topic)
    assert gone.kind is Kind.NEED_USER and gone.reply == NO_TOPIC_SHOW_REPLY


def test_a_different_page_is_a_correction_that_replaces_the_interim_one(svc_cli: Services) -> None:
    # An earlier night of the tour stands in until the show's own page exists; that page then replaces it everywhere.
    own_id = "7c1d2e3"
    own_url = f"https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-san-francisco-ca-{own_id}.html"
    own_page = {**page([{"name": "Third Rail"}, {"name": "First Light"}], event_date="14-11-2026"), "id": own_id}
    wire(svc_cli)
    svc_cli.setlistfm.setlists[own_id] = {**own_page, "url": own_url}  # type: ignore[union-attr]
    topic = "-1001234567890:7"
    start = datetime(2026, 11, 14, 20, 30, tzinfo=TZ)
    tickets = "https://www.ticketweb.com/event/zed-fox/123"
    e = svc_cli.calendar.seed_event(
        "cal-concerts",
        summary=ARTIST,
        start=start,
        description=f"Tickets: {tickets}\nPartiful: https://partiful.com/e/pf1",
    )
    topic_remember(
        topic,
        make_topic_entry(event_id=e["id"], calendar_id="cal-concerts", summary=ARTIST, start=start, now=start),
        svc_cli,
    )
    partiful = FakePartiful()
    partiful.events.append({"customFields": [{"icon": "link", "value": "Tickets", "url": tickets}]})
    svc_cli.partiful = partiful

    interim = setlist_playlist_outcome(PAGE_URL, "", "", "", svc_cli, topic=topic)
    assert interim.kind is Kind.OK and interim.data["rebuilt"] is False
    played = "https://open.spotify.com/playlist/pl1"
    assert [r["uri"] for r in svc_cli.spotify.items["pl1"]] == [
        "spotify:track:t1",
        "spotify:track:c1",
        "spotify:track:t3",
    ]

    fixed = setlist_playlist_outcome(own_url, "", "", "", svc_cli, topic=topic)
    assert fixed.kind is Kind.OK and fixed.data["rebuilt"] is True
    assert fixed.reply == (
        f"**Setlist playlist** [2026-11-14 | Zed Fox]({played}): 2 songs (rebuilt from this page).\n"
        "Calendar event updated with the links. The Partiful has the links too."
    )
    # The playlist is the show's own setlist, in its order; nothing of the other night stays.
    assert [r["uri"] for r in svc_cli.spotify.items["pl1"]] == ["spotify:track:t3", "spotify:track:t1"]
    assert len(svc_cli.spotify.created) == 1
    # One setlist.fm link on the event and on the invite, and it is the new page.
    assert svc_cli.calendar.events["cal-concerts"][0]["description"] == (
        f"Tickets: {tickets}\nPartiful: https://partiful.com/e/pf1\nsetlist.fm: {own_url}\nSetlist playlist: {played}"
    )
    assert partiful.events[0]["customFields"] == [
        {"icon": "link", "value": "Tickets", "url": tickets},
        {"icon": "link", "value": "setlist.fm", "url": own_url},
        {"icon": "music-note", "value": "Setlist playlist", "url": played},
    ]
    # The same page again is no correction: the playlist is topped up, not rebuilt.
    same = setlist_playlist_outcome(own_url, "", "", "", svc_cli, topic=topic)
    assert same.data["rebuilt"] is False and "(already existed, 0 added)" in same.reply


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
        "Calendar event updated with the links."
    )
    assert svc_cli.calendar.patched[0][2] == {
        "description": f"setlist.fm: {PAGE_URL}\nSetlist playlist: https://open.spotify.com/playlist/pl2"
    }


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


def test_a_new_setlist_playlist_carries_the_read_date_and_a_later_read_moves_it(svc_cli: Services) -> None:
    wire(svc_cli)
    sp = svc_cli.spotify
    res = sync_setlist_playlist(artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli)
    assert res.status == "created"
    first = setlist_description(PAGE_URL, today(svc_cli))
    made = [c for c in sp.calls if c[0] == "create_playlist"]
    assert made and made[0][1]["description"] == first

    # The same day: nothing to rewrite.
    sp.described.clear()
    sync_setlist_playlist(artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli)
    assert sp.described == []

    # A week later the page is read again: the day in the description follows.
    svc_cli.clock.advance(7 * 86400)
    sync_setlist_playlist(artist=ARTIST, date=DATE, songs=songs_of(page()), setlistfm_url=PAGE_URL, svc=svc_cli)
    later = setlist_description(PAGE_URL, today(svc_cli))
    assert later != first and sp.described == [(res.id, later)]
