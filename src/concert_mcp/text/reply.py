"""Every reply string the user sees (unit text-render). Pure: no I/O.

Telegram renders **bold** and [text](url); every key is bold, every row a bullet.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from concert_mcp.result import NO_REPLY_TEXT

if TYPE_CHECKING:
    from concert_mcp.flows.poll import PollReport
    from concert_mcp.flows.queue import QueueEntry, QueueStatus
    from concert_mcp.text.artists import TeaserResult
    from concert_mcp.text.candidates import Candidate
    from concert_mcp.text.lineup import Lineup
    from concert_mcp.text.setlists import SetlistResult, SyncResult

__all__ = [
    "LINK_LINE_PREFIXES",
    "NO_CANDIDATES_REPLY",
    "NO_REPLY_TEXT",
    "WHICH_EVENT_REPLY",
    "bullet",
    "bullets",
    "dropped_note",
    "event_link",
    "provider_reply",
    "render_added",
    "render_body",
    "render_candidates",
    "render_dry_add",
    "render_lineup",
    "render_none_found",
    "render_poll",
    "render_queue",
    "render_removed",
    "render_setlist_playlist_announce",
    "render_setlist_standalone",
    "render_teaser_standalone",
    "render_would_look",
    "setlist_bullet",
    "setlist_line",
    "teaser_bullet",
    "teaser_line",
]

LINK_LINE_PREFIXES = ("Teaser playlist: ", "Setlist: ", "Setlist playlist: ")
SETLIST_PLAYLIST_LINE_PREFIX = "Setlist playlist: "
NO_CANDIDATES_REPLY = (
    "I do not have a list to pick from any more. Tell me the event name and date and I will look again."
)
WHICH_EVENT_REPLY = "Which event? Tell me the artist or title (and the date if you know it) and I will remove it."

EVENTEDIT_URL = "https://calendar.google.com/calendar/u/0/r/eventedit/{eid}"
MAX_KEY_LEN = 24
REASON_LEN = 160


def bullet(key: str, value: str) -> str:
    """'• **Key:** value'."""
    return f"• **{key}:** {value}"


def bullets(title: str, when: str, location: str, lines: Sequence[str]) -> str:
    """Artist / When / Where bullets, then one bullet per line (a short 'Key: value' keeps its bold key). The
    description's Headliner line repeats the Artist bullet, so the reply skips it."""
    rows = [bullet("Artist", title), bullet("When", when), bullet("Where", location)]
    for ln in lines:
        if ln.lower().startswith("headliner:"):
            continue
        k, sep, v = ln.partition(": ")
        rows.append(bullet(k, v) if sep and len(k) <= MAX_KEY_LEN else f"• {ln}")
    return "\n".join(rows)


def event_link(html_link: str | None) -> str | None:
    """The eventedit form of a Google Calendar link: the only form that opens the event in the phone app.

    Tested on a phone on 2026-10-25: the API htmlLink, the same eid on calendar.google.com, the day view and the share
    template open the Google Calendar phone app but not the event; only eventedit opens the event. This is the only
    link form this package emits. Do not add fallback forms.

    None and "" come back unchanged, as does a link that carries no eid.
    """
    if not html_link:
        return html_link
    eid = parse_qs(urlparse(html_link).query).get("eid", [None])[0]
    return EVENTEDIT_URL.format(eid=eid) if eid else html_link


def _notes(notes: Sequence[str]) -> str:
    return "".join("\n" + n for n in notes)


def render_added(
    *,
    calendar: str,
    body: str,
    notes: Sequence[str],
    html_link: str | None,
    duplicate: bool,
    updated: bool = False,
) -> str:
    """'**Added to X**', '**Already on X** (nothing new created)' or '**Updated on X**' + body + notes + link."""
    if duplicate:
        head = f"**Already on {calendar}** (nothing new created)"
    elif updated:
        head = f"**Updated on {calendar}**"
    else:
        head = f"**Added to {calendar}**"
    return f"{head}\n{body}{_notes(notes)}\n[Open in Google Calendar]({event_link(html_link)})"


def render_dry_add(calendar: str, body: str, notes: Sequence[str]) -> str:
    """'**Would add to X**' + body + notes."""
    return f"**Would add to {calendar}**\n{body}{_notes(notes)}"


def changed_note(changed: Sequence[str]) -> str:
    """'Changed: title DICE -> Zed Fox; when' or 'Nothing changed; the description was refreshed'."""
    return f"Changed: {'; '.join(changed)}" if changed else "Nothing changed; the description was refreshed"


def dropped_note(dropped: Sequence[str]) -> str:
    """'Left out (not in the text I was given): a; b'."""
    return f"Left out (not in the text I was given): {'; '.join(dropped)}"


def teaser_bullet(res: TeaserResult | None, *, timeout_s: int, dry: bool) -> str | None:
    """The Teaser bullet for every TeaserResult status; None when no teaser was wanted."""
    if res is None:
        return None
    if res.status == "unavailable":
        return bullet("Teaser", "not made (Spotify is not set up: run `concert-mcp spotify auth`)")
    if res.status == "timeout":
        return bullet("Teaser", f"not made (Spotify took longer than {timeout_s} s)")
    if res.status == "failed":
        return bullet("Teaser", f"not made ({(res.reason or 'unknown error')[:REASON_LEN]})")
    if res.status == "none":
        return bullet("Teaser", f"none, no liked songs by {res.artist.name if res.artist else '?'} yet")
    if res.status == "dry_run" or dry or not res.url:
        return bullet("Teaser", f"would make {res.name} ({res.liked_count} liked songs)")
    verb = "" if res.status == "created" else " (already existed, topped up)"
    return bullet(
        "Teaser",
        f"[{res.name}]({res.url}) {res.liked_count} liked songs{verb}. Move it to Concerts › Teasers in Spotify.",
    )


def teaser_line(res: TeaserResult | None) -> str | None:
    """'Teaser playlist: <url>' for a created or updated playlist."""
    if res is not None and res.status in ("created", "updated") and res.url:
        return f"Teaser playlist: {res.url}"
    return None


def setlist_bullet(res: SetlistResult | None, *, queued: bool = True) -> str | None:
    """The Setlist bullet for every SetlistResult; None when no lookup was wanted.

    `queued` adds the "checking weekly for 2 months" clause: true for concert_add, which queues the show for the
    weekly poller, false for the standalone setlist_find tool, which queues nothing and must not promise a check.
    """
    if res is None:
        return None
    if res.match == "unavailable":
        return bullet("Setlist", "lookup skipped (setlist.fm is not set up: run `concert-mcp spotify setlist-auth`)")
    if res.match == "failed":
        return bullet("Setlist", f"lookup failed ({(res.reason or 'unknown error')[:REASON_LEN]})")
    weekly = "; checking weekly for 2 months" if queued else ""
    if not res.found:
        return bullet("Setlist", f"not on setlist.fm yet{weekly}")
    n = res.song_count
    what = f"{n} songs so far" if n else "no songs yet"
    return bullet("Setlist", f"[setlist.fm]({res.url}) {what}{weekly}")


def setlist_line(res: SetlistResult | None) -> str | None:
    """'Setlist: <url>' when found."""
    if res is not None and res.found and res.url:
        return f"Setlist: {res.url}"
    return None


def render_body(title: str, when: str, location: str, lines: Sequence[str], extras: Sequence[str | None]) -> str:
    """bullets() without the link lines, then each extra bullet (teaser, setlist) that is not None."""
    body = bullets(title, when, location, [ln for ln in lines if not ln.startswith(LINK_LINE_PREFIXES)])
    return body + "".join("\n" + e for e in extras if e)


def render_removed(c: Candidate, dry: bool) -> str:
    """The legacy removed_reply: head, Artist, When, Where (if any), one Teaser row per teaser line left in place."""
    head = f"**Would remove from {c.calendar}**" if dry else f"**Removed from {c.calendar}**"
    rows = [bullet("Artist", c.summary), bullet("When", c.when)]
    if c.location:
        rows.append(bullet("Where", c.location))
    for ln in c.description.splitlines():
        if ln.startswith("Teaser playlist: "):
            url = ln.split(": ", 1)[1]
            hint = f'[playlist]({url}) left in place; say "delete the teaser playlist" to remove it'
            rows.append(bullet("Teaser", hint))
    return head + "\n" + "\n".join(rows)


def render_candidates(title: str, cands: Sequence[Candidate]) -> str:
    """The numbered list the user picks from."""
    rows = [
        f"{i}. **{c.summary}** on {c.calendar}: {c.when}" + (f", {c.location}" if c.location else "")
        for i, c in enumerate(cands, 1)
    ]
    return f"I found {len(cands)} events like **{title}**. Reply with the number to delete:\n" + "\n".join(rows)


def _around(date: str | None) -> str:
    return f" around {date}" if date else ""


def render_none_found(title: str, date: str | None, calendar_names: Sequence[str]) -> str:
    """Nothing matched on the calendars searched."""
    return f"I found no event like **{title}**{_around(date)} on {' or '.join(calendar_names)}. Nothing deleted."


def render_would_look(title: str, date: str | None, calendar_names: Sequence[str]) -> str:
    """The dry-run reply for a delete that searched nothing."""
    return f"**Would look for** {title}{_around(date)} on {' and '.join(calendar_names)}, then remove the one match."


def render_teaser_standalone(res: TeaserResult, *, timeout_s: int, dry: bool) -> str:
    """The teaser_playlist tool's reply."""
    return "**Teaser playlist**\n" + (teaser_bullet(res, timeout_s=timeout_s, dry=dry) or "")


def render_setlist_playlist(res: SyncResult, *, setlistfm_url: str, event: str | None, dry: bool = False) -> str:
    """The setlist_playlist tool's reply: the playlist link, how many songs landed, which are not on Spotify,
    and whether the calendar event got the link (`event` is None when no event was found, else a short note)."""
    head = "**Setlist playlist** "
    if res.status == "unavailable":
        return head + "not made (Spotify is not set up: run `concert-mcp spotify auth`)"
    if res.status == "failed":
        return head + f"not made ({(res.reason or 'unknown error')[:REASON_LEN]})"
    if res.status == "dry_run" or dry or not res.url:
        songs = f"the {res.total} songs" if res.total else "the songs"
        return head + f"would make {res.name} from {songs} at {setlistfm_url}"
    # Created or topped up: the link, the counts, the misses, the cross-link, the event.
    n = res.on_spotify
    text = head + f"[{res.name}]({res.url}): {n} song{'s' if n != 1 else ''}"
    if res.missing:
        text += f", {len(res.missing)} not on Spotify"
    if res.status == "updated":
        text += f" (already existed, {res.added} added)"
    text += "."
    if res.missing:
        text += "\nNot on Spotify: " + ", ".join(res.missing)
    if res.teaser_url:
        text += "\nLinked with the teaser playlist."
    text += "\n" + (event or "No calendar event found for this show; the link was not added to one.")
    return text


def render_lineup(artist: str, date: str, venue: str, lineup: Lineup, *, patched: bool) -> str:
    """The lineup tool's reply: the openers (each with its recent setlist.fm page), the set times, the sources."""
    from concert_mcp.text.lineup import expected_line

    where = f" @ {venue}" if venue else ""
    head = f"**Lineup:** {artist}{where}, {date}"
    if lineup.status == "none":
        return head + "\nNo opener or set times announced yet in the sources I can find."
    rows: list[str] = []
    for o in lineup.openers:
        page = f" ([recent setlist]({o.setlist_url}))" if o.setlist_url else ""
        rows.append(bullet("Opener", o.name + page))
    if not lineup.openers:
        rows.append(bullet("Opener", "none announced"))
    exp = expected_line(lineup, artist)
    rows.append(bullet("Expected", exp.partition(": ")[2] if exp else "set times not announced"))
    if lineup.sources:
        rows.append(bullet("Sources", " ".join(f"[{i}]({u})" for i, u in enumerate(lineup.sources[:3], 1))))
    tail = "\nAdded to the calendar event." if patched else ""
    return head + "\n" + "\n".join(rows) + tail


def render_setlist_standalone(res: SetlistResult) -> str:
    """The setlist_find tool's reply: no weekly-check promise (nothing is queued); a miss points at the search page."""
    search = f" Search: {res.search_url}" if not res.found and res.search_url and res.match == "none" else ""
    return "**Setlist**\n" + (setlist_bullet(res, queued=False) or "") + search


def _queue_row(e: QueueEntry, expired: bool) -> str:
    row = f"• {e.date} {e.artist} @ {e.venue} — {e.song_count} songs, checking until {e.until}"
    return row + (" (expired)" if expired else "")


def render_queue(status: QueueStatus) -> str:
    """The setlist queue: active shows first, then expired ones, each by date; last the weekly poll's last run."""
    last = f"Last poll: {status.last_poll or 'never'}"
    if not status.entries:
        return f"The setlist queue is empty.\n{last}"
    rows = [_queue_row(e, False) for e in sorted(status.active, key=lambda e: e.date)]
    rows += [_queue_row(e, True) for e in sorted(status.expired, key=lambda e: e.date)]
    return f"**Setlist queue** ({len(status.entries)} shows)\n" + "\n".join(rows) + f"\n{last}"


def render_poll(report: PollReport) -> str:
    """The weekly poll's report: one line per queued show, or why the run was skipped."""
    last = report.last_poll or "never"
    if not report.ran:
        return (
            f"**Setlist poll** skipped: last run {last}, less than {report.interval_days} days ago "
            "(`concert-mcp poll --force` runs it now)"
        )
    head = "**Setlist poll (dry run)**" if report.dry_run else "**Setlist poll**"
    head += f" {report.now} (last: {last})"
    if not report.rows:
        return f"{head}\nThe setlist queue is empty."
    return head + "".join(f"\n• {r.date} {r.artist} — {r.action}" for r in report.rows)


def render_setlist_playlist_announce(
    artist: str,
    date: str,
    venue: str,
    *,
    setlist_url: str,
    song_count: int,
    playlist_name: str,
    playlist_url: str,
    html_link: str | None,
) -> str:
    """The one Telegram message the weekly poll sends when a show's setlist playlist first exists."""
    rows = [
        bullet("Setlist", f"[setlist.fm]({setlist_url}) {song_count} songs"),
        bullet("Playlist", f"[{playlist_name}]({playlist_url})"),
    ]
    if html_link:
        rows.append(bullet("Calendar", f"[Open in Google Calendar]({event_link(html_link)})"))
    where = f" @ {venue}" if venue else ""
    return f"**Setlist playlist:** {artist}{where}, {date}\n" + "\n".join(rows)


def provider_reply(provider: str, operator: str, *, what: str = "request") -> str:
    """The user-facing line for a provider failure, with the operator's next step."""
    match provider:
        case "gws":
            return f"Google Calendar refused the {what}. {operator}: check `gws auth` on the host."
        case "gws-nojson":
            return f"Google Calendar gave an unreadable answer. {operator}: check the host."
        case "gws-keychain":
            return (
                "The login keychain is locked in this session, so I did not touch Google Calendar. "
                f"{operator}: create the gws file keyring key (README.md#google-calendar) "
                "or run this from a session with keychain access."
            )
        case "gws-missing":
            return "The calendar tool is not reachable right now. Try again in a minute."
        case "spotify-unconfigured":
            return f"Spotify is not set up on this machine. {operator}: run `concert-mcp spotify auth`."
        case "setlistfm-unconfigured":
            return f"setlist.fm is not set up on this machine. {operator}: run `concert-mcp spotify setlist-auth`."
        case "spotify":
            return f"Spotify refused the request. {operator}: check the host log."
        case "setlistfm":
            return f"setlist.fm refused the request. {operator}: check the host log."
        case "places-unconfigured":
            return f"Google Places is not set up on this machine. {operator}: run `concert-mcp venues auth`."
        case "places" | "nominatim":
            return f"The venue lookup failed; the event keeps the venue as written. {operator}: check the host log."
        case "gemini-unconfigured":
            return f"Gemini is not set up on this machine. {operator}: run `concert-mcp gemini auth`."
        case "gemini":
            return f"The lineup lookup failed; the event keeps what the ticket said. {operator}: check the host log."
        case "op":
            return f"1Password did not answer. {operator}: run the auth command with --manual."
        case _:
            return f"{provider} failed. {operator}: check the host log."
