"""The weekly setlist poll (unit flows-music): every queued show, once a week, until two months after it.

A setlist.fm page fills in over weeks (one person adds a few songs, others finish it), so every concert stays in the
queue until `until` and is checked on every run, not only the empty pages. The first run that finds songs makes the
setlist playlist (`sync_setlist_playlist`, from the page's songs), writes `Setlist playlist: <url>` into the calendar
event and sends the Concerts group one message with the link; later runs top the playlist up without a word. A run
less than a week after the last one is a no-op unless forced, so the launchd job can fire daily. Everything here is
idempotent: the queue row remembers the page, the song count and the playlist, and the calendar patch and the message
happen once per show.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from concert_mcp.flows.deliver import deliver
from concert_mcp.flows.queue import (
    QueueEntry,
    is_expired,
    queue_forget,
    queue_list,
    queue_replace,
    read_poll_stamp,
    write_poll_stamp,
)
from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist import find_setlist
from concert_mcp.flows.setlist_playlist import sync_setlist_playlist
from concert_mcp.flows.topics import topic_for_event
from concert_mcp.log import log_info, log_warn
from concert_mcp.result import FixInput, Kind, Outcome, ProviderError, crash_outcome
from concert_mcp.text.candidates import missing_link_lines, patched_description
from concert_mcp.text.naming import playlist_name
from concert_mcp.text.reply import SETLIST_PLAYLIST_LINE_PREFIX, render_poll, render_setlist_playlist_announce
from concert_mcp.text.routes import ChatTarget
from concert_mcp.text.setlists import SetlistResult, Song, SyncResult, songs_of

POLL_INTERVAL_DAYS = 7
REASON_LIMIT = 160


SyncFn = Callable[[QueueEntry, SetlistResult, Services], SyncResult]


def _songs_for(setlist: SetlistResult, svc: Services) -> list[Song]:
    """The page's songs with covers and tape marked (one `get_setlist`); the search row's names when that fails."""
    row = setlist.setlist
    if row is None:
        return []
    if svc.setlistfm is not None and row.id:
        try:
            songs = songs_of(svc.setlistfm.get_setlist(row.id))
        except ProviderError as e:
            log_warn(f"setlist page not fetched; using the search row | id='{row.id}' error='{e.error}'")
            songs = []
        if songs:
            return songs
    return [Song(name=name) for name in row.songs]


def sync_playlist(entry: QueueEntry, setlist: SetlistResult, svc: Services) -> SyncResult:
    """`sync_setlist_playlist` for a queue row: the show's playlist, created or topped up; never raises."""
    name = playlist_name(entry.date, entry.artist)
    try:
        songs = _songs_for(setlist, svc)
        return sync_setlist_playlist(
            artist=entry.artist, date=entry.date, songs=songs, setlistfm_url=setlist.url or "", svc=svc
        )
    except Exception as e:  # noqa: BLE001 - one show's playlist must not take the poll down
        return SyncResult("failed", name, setlist.song_count, reason=f"{type(e).__name__}: {e}"[:REASON_LIMIT])


@dataclass(frozen=True)
class PollRow:
    artist: str
    date: str
    action: str
    setlistfm_url: str | None = None
    playlist_url: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "artist": self.artist,
            "date": self.date,
            "action": self.action,
            "setlistfm_url": self.setlistfm_url,
            "playlist_url": self.playlist_url,
        }


@dataclass(frozen=True)
class PollReport:
    ran: bool
    now: str
    last_poll: str | None
    rows: tuple[PollRow, ...]
    dry_run: bool = False
    interval_days: int = POLL_INTERVAL_DAYS

    def to_json(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "now": self.now,
            "last_poll": self.last_poll,
            "dry_run": self.dry_run,
            "rows": [r.to_json() for r in self.rows],
        }


def _iso(when: datetime | None) -> str | None:
    return when.isoformat(timespec="seconds") if when else None


def poll_setlists(
    svc: Services,
    now: datetime,
    *,
    force: bool = False,
    dry_run: bool = False,
    send: bool = True,
    sync: SyncFn | None = None,
) -> PollReport:
    """One weekly run over the whole queue; a no-op within `POLL_INTERVAL_DAYS` of the last run unless forced."""
    sync = sync or sync_playlist
    last = read_poll_stamp(svc)
    today = now.astimezone(svc.tz).date()
    if last is not None and not force and (today - last.astimezone(svc.tz).date()).days < POLL_INTERVAL_DAYS:
        log_info(f"setlist poll skipped | last='{_iso(last)}'")
        return PollReport(ran=False, now=_iso(now) or "", last_poll=_iso(last), rows=(), dry_run=dry_run)
    rows = tuple(
        _poll_entry(entry, svc, today, sync=sync, dry_run=dry_run, send=send)
        for entry in sorted(queue_list(svc), key=lambda e: e.date)
    )
    if not dry_run:
        write_poll_stamp(now, svc)
    return PollReport(ran=True, now=_iso(now) or "", last_poll=_iso(last), rows=rows, dry_run=dry_run)


def _poll_entry(entry: QueueEntry, svc: Services, today: date, *, sync: SyncFn, dry_run: bool, send: bool) -> PollRow:
    """One show: drop it when past `until`; else look the page up and make or top up the playlist."""

    def row(action: str, e: QueueEntry = entry) -> PollRow:
        return PollRow(e.artist, e.date, action, setlistfm_url=e.setlistfm_url, playlist_url=e.playlist_url)

    if is_expired(entry, today):
        if dry_run:
            return row("would drop (expired)")
        queue_forget(entry.event_id, svc)
        return row("expired; dropped from the queue")
    try:
        res = find_setlist(entry.artist, entry.date, entry.venue or None, svc)
    except FixInput as e:
        return row(f"skipped: {e.error}")
    if res.match == "unavailable":
        return row("lookup skipped (setlist.fm is not set up)")
    if res.match == "failed":
        return row(f"lookup failed: {res.reason}")
    if not res.found:
        return row("not on setlist.fm yet")

    n = res.song_count
    seen = replace(entry, setlistfm_url=res.url, song_count=n)
    first = entry.playlist_url is None
    grew = n > entry.song_count
    if n == 0 or (not first and not grew):
        if not dry_run and seen != entry:
            queue_replace(seen, svc)
        return row("page found, no songs yet" if n == 0 else f"{n} songs; playlist unchanged", seen)
    if dry_run:
        what = "would make the playlist and announce it" if first else "would top the playlist up"
        return row(f"{n} songs; {what}", seen)

    ps = sync(entry, res, svc)
    if ps.status not in ("created", "updated") or not ps.url:
        queue_replace(seen, svc)  # the count is remembered; `first` stays true, so next week tries again
        return row(f"{n} songs; playlist not made ({ps.reason or ps.status})", seen)
    done = replace(seen, playlist_url=ps.url)
    queue_replace(done, svc)
    if not first:
        return row(
            f"{n} songs; {ps.added} added to the playlist" if ps.added else f"{n} songs; playlist unchanged", done
        )

    made = "playlist created" if ps.status == "created" else "playlist linked"
    note, html_link = _patch_event(entry, ps.url, svc)
    status = _announce(entry, res, ps, html_link, svc, send=send)
    extra = f"; {note}" if note else ""
    return row(f"{n} songs; {made}; {status}{extra}", done)


def _patch_event(entry: QueueEntry, url: str, svc: Services) -> tuple[str | None, str | None]:
    """Write `Setlist playlist: <url>` into the event once; returns (a note when it could not, the event's htmlLink)."""
    try:
        e = svc.calendar.get_event(entry.calendar_id, entry.event_id)
    except ProviderError as exc:
        return f"event not patched ({str(exc.error)[:REASON_LIMIT]})", None
    if not e.get("id") or e.get("status") == "cancelled":
        return "event gone from the calendar", None
    missing = missing_link_lines(e.get("description"), [f"{SETLIST_PLAYLIST_LINE_PREFIX}{url}"])
    if missing:
        try:
            svc.calendar.patch_event(
                entry.calendar_id, entry.event_id, description=patched_description(e.get("description"), missing)
            )
        except ProviderError as exc:
            return f"event not patched ({str(exc.error)[:REASON_LIMIT]})", e.get("htmlLink")
    return None, e.get("htmlLink")


def _announce_target(entry: QueueEntry, svc: Services) -> ChatTarget | None:
    """The topic the show was added from, else the configured Concerts chat, else nowhere."""
    key = topic_for_event(entry.event_id, svc)
    if key:
        chat, _, thread = key.partition(":")
        return ChatTarget(channel="telegram", chat=chat, thread=thread or None, account=None)
    if svc.settings.concerts_chat:
        return ChatTarget(channel="telegram", chat=svc.settings.concerts_chat, thread=None, account=None)
    return None


def _announce(
    entry: QueueEntry, res: SetlistResult, ps: SyncResult, html_link: str | None, svc: Services, *, send: bool
) -> str:
    """The one message with the link, through deliver (dedupe and the sent log included)."""
    target = _announce_target(entry, svc)
    if target is None:
        return "not announced (no chat: set CONCERT_MCP_CONCERTS_CHAT)"
    text = render_setlist_playlist_announce(
        entry.artist,
        entry.date,
        entry.venue.split(",")[0].strip(),
        setlist_url=res.url or "",
        song_count=res.song_count,
        playlist_name=ps.name,
        playlist_url=ps.url or "",
        html_link=html_link,
    )
    delivery = deliver(Outcome(Kind.OK, reply=text), svc, send=svc.settings.send and send, target=target)
    if delivery.sent:
        return "announced"
    log_warn(f"setlist playlist not announced | artist='{entry.artist}' status='{delivery.status}'")
    return f"not announced ({delivery.status})"


def poll_outcome(
    svc: Services, *, force: bool = False, dry_run: bool = False, send: bool = True, sync: SyncFn | None = None
) -> Outcome:
    """The `concert-mcp poll` Outcome: the report as the reply, its JSON as the data."""
    try:
        report = poll_setlists(svc, svc.clock.now(), force=force, dry_run=dry_run, send=send, sync=sync)
    except Exception as e:  # noqa: BLE001 - a tool never raises
        return crash_outcome(e, svc.operator)
    return Outcome(Kind.DRY_RUN if dry_run else Kind.OK, reply=render_poll(report), data=report.to_json())
