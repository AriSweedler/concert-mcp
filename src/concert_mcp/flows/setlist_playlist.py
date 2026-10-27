"""The setlist playlist flow (unit flows-music): a Spotify playlist in setlist.fm order for one show.

The owner pastes a setlist.fm link (of this show, or of an equivalent show on the same tour) or names the show; the
songs come from the setlist.fm API, each is found on Spotify by `track:<name> artist:<artist>` search, and the
playlist named "YYYY-MM-DD | Artist" is created or topped up with the ones it lacks, setlist order kept. The teaser
playlist of the same show has the same name: a setlist playlist's description starts with `Setlist:`, a teaser's with
`Teaser for`, and that first word is what tells them apart. The two descriptions link each other, and the calendar
event's description gains `Setlist playlist: <url>` once. `sync_setlist_playlist` is the piece the weekly poll reuses.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from concert_mcp.flows.event_delete import DeleteRequest, find_candidates
from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist import find_setlist
from concert_mcp.flows.topics import live_event, topic_event
from concert_mcp.log import log_info, log_warn
from concert_mcp.result import FixInput, Kind, NeedUser, Outcome, ProviderError, crash_outcome, need, outcome_from
from concert_mcp.text.artists import find_playlist_named, new_uris
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

REASON_LIMIT = 160
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

    Idempotent: the playlist is found by name and marker, only the URIs it lacks are added (setlist order kept for
    the new tail), and the cross-links are written once. Tape songs (walk-on music) are not searched.
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
        missing: list[str] = []
        for song in songs:
            if song.tape:
                continue
            hits = api.search_tracks(track_query(song, artist), 1)
            uri = str(hits[0].get("uri") or "") if hits else ""
            if not uri:
                missing.append(song.name)
            elif uri not in uris:
                uris.append(uri)

        if existing:
            pl = existing
            log_info(f"setlist playlist exists; topping it up | name='{name}' id='{pl['id']}'")
        else:
            pl = api.create_playlist(
                name=name, description=setlist_description(artist, date, setlistfm_url, teaser_url), public=False
            )
            log_info(f"setlist playlist created | name='{name}' id='{pl['id']}'")
        url = _url_of(pl)
        have = {r["uri"] for r in api.playlist_items(pl["id"])}
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
            already_there=len(uris) - len(new),
            missing=tuple(missing),
            teaser_url=teaser_url,
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
