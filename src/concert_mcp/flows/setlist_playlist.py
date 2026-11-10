"""The setlist playlist flow (unit flows-music): a Spotify playlist in setlist.fm order for one show.

The owner pastes a setlist.fm link (of this show, or of an equivalent show on the same tour) or names the show; the
songs come from the setlist.fm API, each is found on Spotify by a `track:<name> artist:<artist>` search and, when
that finds nothing by the artist, by a plain `<name> <artist>` search whose candidates are checked by loose name
(case, accents and punctuation ignored: `K.Flay` is `K Flay`, `Don’t` is `Don't`) and by the artist's Spotify id.
Among the matches the pick is the version the artist originally released (the plain title over `- Seattle Sessions`,
the album over the single, the earliest release). The playlist named "YYYY-MM-DD | Artist" is created or topped up with
the ones it lacks, setlist order kept; a row an earlier run filled with another version is swapped in place. The teaser
playlist of the same show has the same name: a setlist playlist's description starts with `Setlist:`, a teaser's with
`Teaser for`, and that first word is what tells them apart. The two descriptions link each other, and the calendar
event's description gains `Setlist playlist: <url>` once. `sync_setlist_playlist` is the piece the poll reuses.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from concert_mcp.adapters.protocols import SpotifyApi
from concert_mcp.flows.event_delete import DeleteRequest, find_candidates
from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist import find_setlist
from concert_mcp.flows.teaser import resolve_artist
from concert_mcp.flows.topics import live_event, topic_event
from concert_mcp.log import log_debug, log_info, log_warn
from concert_mcp.result import FixInput, Kind, NeedUser, Outcome, ProviderError, crash_outcome, need, outcome_from
from concert_mcp.text.artists import NoArtistMatch, find_playlist_named, new_uris
from concert_mcp.text.candidates import missing_link_lines, patched_description
from concert_mcp.text.dates import concert_date
from concert_mcp.text.naming import (
    SETLIST_LINK,
    is_setlist_playlist,
    playlist_name,
    setlist_description,
    with_setlist_link,
)
from concert_mcp.text.reply import provider_reply, render_setlist_playlist
from concert_mcp.text.routes import chat_target, forum_target
from concert_mcp.text.setlists import Song, SyncResult, page_artist_of, setlist_id_of, setlist_row, songs_of
from concert_mcp.text.topics import TopicEntry, topic_key
from concert_mcp.text.tracks import candidate_summary, pick_track, plain_query, stale_rows, track_label

REASON_LIMIT = 160
# Candidates read per search: the first Spotify result is not always the artist's own recording, and the original
# release of a song may sit behind a session take, a remix, a softer version and the single of the same name.
CANDIDATES = 10
NOT_SETLISTFM_FIX = (
    'send a setlist.fm setlist page, like "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-'
    'san-francisco-ca-3bd6f4a.html", or leave url empty and pass artist and date'
)
NO_PAGE_FIX = "that setlist.fm link did not resolve; paste the page's address from the browser"
NO_SONGS_FIX = "that setlist.fm page has no songs yet; paste a filled-out page of the same tour, or try after the show"
NO_SETLIST_REPLY = (
    "No setlist.fm page for {artist} on {date} yet. Paste one when it appears and I will build the playlist."
)
EVENT_PATCHED = "Calendar event updated with the link."
EVENT_HAD_LINK = "Calendar event already had the link."


def find_setlist_playlist(playlists: Iterable[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    """The setlist playlist of that name (description starts with `Setlist:`)."""
    return find_playlist_named([pl for pl in playlists if is_setlist_playlist(pl)], name)


def find_teaser_playlist(playlists: Iterable[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    """The teaser playlist of that name (any playlist of the name that is not a setlist playlist)."""
    return find_playlist_named([pl for pl in playlists if not is_setlist_playlist(pl)], name)


def track_query(song: Song, artist: str) -> str:
    """The Spotify search for one setlist song: the cover's original artist when it is a cover."""
    return f"track:{song.name} artist:{song.artist or artist}"


def find_track_uri(song: Song, artist: str, api: SpotifyApi, *, artist_id: str | None = None) -> str:
    """The Spotify URI of one setlist song, or "" when no candidate is the song by its artist.

    Two searches, the second only when the first yields nothing by the artist: the field-filtered
    `track:<name> artist:<artist>`, then the plain `<name> <artist>` (Spotify's `artist:` filter misses some
    spellings, `K.Flay` among them). Every candidate must match the song by loose name and credit the artist, by
    Spotify id when the headliner's is known, else by loose name; a cover is matched to its original artist by name.
    Among the matches the pick is the version the artist originally released (`rank_key`). The query, the candidates
    and the pick with its album and year are logged at debug level, so a `Not on Spotify` row explains itself.
    """
    want_artist = song.artist or artist
    want_id = None if song.artist else artist_id
    for q in (track_query(song, artist), plain_query(song, artist)):
        hits = api.search_tracks(q, CANDIDATES)
        pick = pick_track(hits, song.name, want_artist, want_id)
        log_debug(
            f"setlist song search | song='{song.name}' query='{q}' candidates='{candidate_summary(hits)}' "
            f"picked='{track_label(pick) if pick else None}'"
        )
        uri = str(pick.get("uri") or "") if pick else ""
        if uri:
            return uri
    return ""


def _artist_id(artist: str, api: SpotifyApi) -> str | None:
    """The headliner's Spotify id for matching tracks by credit; None when the name is not an exact artist match."""
    try:
        a, corrected = resolve_artist(artist, api)
    except (NoArtistMatch, ProviderError) as e:
        log_debug(f"setlist artist id not resolved | artist='{artist}' reason='{str(e)[:REASON_LIMIT]}'")
        return None
    return None if corrected else a.id


def _url_of(pl: Mapping[str, Any]) -> str | None:
    return (pl.get("external_urls") or {}).get("spotify")


def sync_setlist_playlist(
    *,
    artist: str,
    date: str,
    songs: Sequence[Song],
    setlistfm_url: str,
    svc: Services,
    dry_run: bool = False,
) -> SyncResult:
    """Create or top up the setlist playlist for the show; never raises except FixInput for a bad date.

    Idempotent: the playlist is found by name and marker, a row holding another version of a song is swapped in place
    for the pick, only the URIs it then lacks are added (setlist order kept for the new tail), and the cross-links are
    written once. Tape songs (walk-on music) are not searched.
    """
    date = concert_date(date)
    name = playlist_name(date, artist)
    if dry_run:
        log_info(f"dry-run: would sync | name='{name}' songs='{len(songs)}'")
        return SyncResult("dry_run", name, len(songs))
    if svc.spotify is None:
        return SyncResult("unavailable", name, len(songs), reason="Spotify is not set up")
    api = svc.spotify
    try:
        playlists = api.my_playlists()
        existing = find_setlist_playlist(playlists, name)
        teaser = find_teaser_playlist(playlists, name)
        teaser_url = _url_of(teaser) if teaser else None

        uris: list[str] = []
        picks: list[tuple[str, str]] = []  # (song name, uri) per found song, for the swap of a stale version
        missing: list[str] = []
        wanted = [s for s in songs if not s.tape]
        artist_id = _artist_id(artist, api) if wanted else None
        for song in wanted:
            uri = find_track_uri(song, artist, api, artist_id=artist_id)
            if not uri:
                missing.append(song.name)
            elif uri not in uris:
                uris.append(uri)
                picks.append((song.name, uri))

        if existing:
            pl = existing
            log_info(f"setlist playlist exists; topping it up | name='{name}' id='{pl['id']}'")
        else:
            pl = api.create_playlist(
                name=name, description=setlist_description(artist, date, setlistfm_url, teaser_url), public=False
            )
            log_info(f"setlist playlist created | name='{name}' id='{pl['id']}'")
        url = _url_of(pl)
        rows = api.playlist_items(pl["id"])
        # A row an earlier run filled with another version of a song (`Black Wave - Seattle Sessions` for `Black
        # Wave`) is swapped for the pick in its own slot, so the setlist order stands and the song is not added twice.
        swaps = stale_rows(rows, picks)
        swapped: list[str] = []
        for position, old, uri, song_name in swaps:
            api.replace_item(pl["id"], position=position, old_uri=old, new_uri=uri)
            log_info(f"setlist track swapped | song='{song_name}' position='{position}' old='{old}' new='{uri}'")
            swapped.append(song_name)
        have = {r["uri"] for r in rows} - {old for _, old, _, _ in swaps} | {uri for _, _, uri, _ in swaps}
        new = new_uris(uris, have)
        if new:
            api.add_items(pl["id"], new)

        # Cross-links, each written once: the teaser's description names the setlist playlist and the setlist
        # playlist's names the teaser (an existing setlist playlist may predate the teaser).
        if teaser and url and SETLIST_LINK not in str(teaser.get("description") or ""):
            api.update_playlist_details(teaser["id"], description=with_setlist_link(teaser.get("description"), url))
        if existing and teaser_url and teaser_url not in str(existing.get("description") or ""):
            api.update_playlist_details(
                existing["id"], description=setlist_description(artist, date, setlistfm_url, teaser_url)
            )
        return SyncResult(
            "updated" if existing else "created",
            name,
            len(songs),
            url=url,
            id=pl["id"],
            added=len(new),
            already_there=len(uris) - len(new) - len(swapped),
            missing=tuple(missing),
            teaser_url=teaser_url,
            swapped=tuple(swapped),
        )
    except ProviderError as e:
        return SyncResult("failed", name, len(songs), reason=str(e.error)[:REASON_LIMIT])
    except Exception as e:  # noqa: BLE001 - the caller's reply must land whatever Spotify does
        return SyncResult("failed", name, len(songs), reason=f"{type(e).__name__}: {e}"[:REASON_LIMIT])


def _topic_entry(svc: Services) -> TopicEntry | None:
    """The live topic's registry entry, when the call comes from a Telegram forum topic that owns an event."""
    _sid, route = svc.context.live_route(svc.clock.time())
    ft = forum_target(chat_target(route)) if route else None
    return topic_event(topic_key(ft[0], ft[1]), svc) if ft else None


def _event_for_show(artist: str, date: str, svc: Services) -> tuple[str, dict[str, Any]] | None:
    """(calendar id, event) of the show: the live topic's event when there is one, else by title and date."""
    entry = _topic_entry(svc)
    if entry:
        e = live_event(entry, svc)
        if e:
            return entry.calendar_id, e
    req = DeleteRequest(title=artist, date=date, calendar=svc.settings.concerts_calendar)
    found = find_candidates(req, svc)
    if not found:
        return None
    e = svc.calendar.get_event(found[0].calendar_id, found[0].id)
    return found[0].calendar_id, e


def link_event(artist: str, date: str, url: str, svc: Services) -> str | None:
    """Patch `Setlist playlist: <url>` into the show's event description once; None when no event was found."""
    try:
        hit = _event_for_show(artist, date, svc)
        if hit is None:
            return None
        cal_id, e = hit
        line = f"{SETLIST_LINK}{url}"
        missing = missing_link_lines(e.get("description"), [line])
        if not missing:
            return EVENT_HAD_LINK
        svc.calendar.patch_event(cal_id, e["id"], description=patched_description(e.get("description"), missing))
        return EVENT_PATCHED
    except ProviderError as e:
        log_warn(f"event not linked | error='{e.error[:200]}'")
        return f"Calendar event not updated: {str(e.error)[:REASON_LIMIT]}"


def _page_date(setlist_json: Mapping[str, Any]) -> str:
    return setlist_row(setlist_json).date


def setlist_playlist_outcome(
    url: str, artist: str, date: str, venue: str, svc: Services, *, dry_run: bool = False
) -> Outcome:
    """The setlist_playlist tool's Outcome: fetch the songs, sync the playlist, link the event, render the reply."""
    try:
        url, artist, date, venue = url.strip(), artist.strip(), date.strip(), venue.strip()
        if not url and not artist:
            raise need("artist")
        if not url and not date:
            raise need("date")
        # In a topic the show's date is the event's: a pasted page may be another night of the same tour.
        entry = _topic_entry(svc)
        if not date and entry:
            date = entry.date

        if url:
            setlist_id = setlist_id_of(url)
            if not setlist_id:
                raise FixInput(f"not a setlist.fm setlist page: {url}", NOT_SETLISTFM_FIX)
        else:
            res = find_setlist(artist, date, venue or None, svc)
            if res.match == "unavailable":
                reply = provider_reply("setlistfm-unconfigured", svc.operator)
                return Outcome(Kind.ERROR, reply=reply, error="setlistfm not configured")
            if res.match == "failed":
                return Outcome(Kind.ERROR, reply=f"Setlist lookup failed: {res.reason}", error=res.reason)
            if not res.found or res.setlist is None:
                reply = NO_SETLIST_REPLY.format(artist=artist, date=concert_date(date))
                return Outcome(Kind.NEED_USER, reply=reply, error="no setlist.fm page")
            setlist_id, url = res.setlist.id, res.setlist.url

        songs: list[Song] = []
        if svc.setlistfm is None:
            if not dry_run:
                reply = provider_reply("setlistfm-unconfigured", svc.operator)
                return Outcome(Kind.ERROR, reply=reply, error="setlistfm not configured")
            artist = artist or page_artist_of(url) or ""  # the plan without the key: the artist from the page's slug
        else:
            page = svc.setlistfm.get_setlist(setlist_id)
            if page is None:
                raise FixInput(f"setlist.fm has no setlist {setlist_id}", NO_PAGE_FIX)
            songs = songs_of(page)
            if not songs:
                raise FixInput(f"setlist {setlist_id} has no songs yet", NO_SONGS_FIX)
            artist = artist or str((page.get("artist") or {}).get("name") or "")
            date = date or _page_date(page)
        if not artist:
            raise need("artist")
        if not date:
            raise need("date")

        res = sync_setlist_playlist(artist=artist, date=date, songs=songs, setlistfm_url=url, svc=svc, dry_run=dry_run)
        if res.status == "unavailable":
            reply = provider_reply("spotify-unconfigured", svc.operator)
            return Outcome(Kind.ERROR, reply=reply, error="spotify not configured", data=res.to_json())
        event = link_event(artist, concert_date(date), res.url, svc) if res.url else None
        reply = render_setlist_playlist(res, setlistfm_url=url, event=event, dry=dry_run)
        if res.status == "failed":
            return Outcome(Kind.ERROR, reply=reply, error=res.reason, data=res.to_json())
        kind = Kind.DRY_RUN if dry_run else Kind.OK
        return Outcome(kind, reply=reply, data={**res.to_json(), "setlistfm_url": url, "event": event})
    except (FixInput, NeedUser, ProviderError) as e:
        return outcome_from(e)
    except Exception as e:  # noqa: BLE001 - a tool never raises
        return crash_outcome(e, svc.operator)
