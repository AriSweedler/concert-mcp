"""A best guess at where a show's setlist will be (unit flows-music).

A show has no setlist.fm page until it has been played, and the user gives that page afterwards (`/setlist`). When
the concert is added, the nearest thing to it is the artist's most recent page there, usually an earlier night of the
same tour. That page is offered as a guess: `concert_add` says `possible setlist.fm link` and the concert's record
keeps it, apart from the page the user gives. Nothing acts on a guess: no playlist is made from it, and it is not a
link on the calendar event or on the invite.
"""

from __future__ import annotations

from concert_mcp.flows.services import Services
from concert_mcp.log import kv, log_warn
from concert_mcp.text.lineup import latest_setlist_url


def guess_setlist_page(artist: str, svc: Services) -> str | None:
    """The artist's most recent setlist.fm page; None when setlist.fm is not set up, has none, or does not answer.
    Never raises: the concert is added whatever setlist.fm does."""
    api = svc.setlistfm
    if api is None:
        return None
    try:
        return latest_setlist_url(api.latest_setlist(artist))
    except Exception as e:  # noqa: BLE001 - a guess is not worth a failed add
        log_warn("setlist guess failed | " + kv(artist=artist, error=f"{type(e).__name__}: {str(e)[:200]}"))
        return None
