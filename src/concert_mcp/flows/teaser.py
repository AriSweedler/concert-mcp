"""The Spotify teaser playlist flow (unit flows-music).

A teaser is every song the user liked by the artist, oldest-liked first, in a private playlist named
"YYYY-MM-DD | Artist". `make_teaser` never raises for a provider or matching failure, because concert_add
calls it and the calendar event must land whatever Spotify does; only a bad date (the caller's input) propagates.
"""

from __future__ import annotations

from typing import Any

from concert_mcp.adapters.protocols import SpotifyApi
from concert_mcp.flows.services import Services
from concert_mcp.log import log_info, log_warn
from concert_mcp.result import FixInput, Kind, NeedUser, Outcome, ProviderError, crash_outcome, need, outcome_from
from concert_mcp.text.artists import (
    Artist,
    NoArtistMatch,
    TeaserResult,
    find_playlist_named,
    liked_hits,
    new_uris,
    pick_artist,
)
from concert_mcp.text.dates import concert_date
from concert_mcp.text.naming import playlist_name, teaser_description
from concert_mcp.text.reply import provider_reply, render_teaser_standalone

REASON_LIMIT = 160


def resolve_artist(name: str, api: SpotifyApi) -> tuple[Artist, bool]:
    """(artist, corrected) from a Spotify search; NoArtistMatch when nothing is close."""
    items = api.search_artists(name, 10)
    artist, corrected = pick_artist(name, items)
    if corrected:
        log_warn(f"artist name corrected | asked='{name}' spotify='{artist.name}'")
    return artist, corrected


def liked_by_artist(artist: Artist, api: SpotifyApi, *, deadline: float | None = None) -> list[dict[str, Any]]:
    """Liked rows by the artist, with one forced refresh when the cache has none."""
    hits = liked_hits(api.liked_rows(deadline=deadline), artist)
    if not hits:
        hits = liked_hits(api.liked_rows(refresh=True, deadline=deadline), artist)
    return hits


def make_teaser(
    artist: str,
    date: str,
    svc: Services,
    *,
    dry_run: bool = False,
    description: str | None = None,
) -> TeaserResult:
    """Create or top up the teaser playlist; never raises except FixInput for a bad date."""
    if svc.spotify is None:
        return TeaserResult("unavailable", None, date, None, 0, reason="Spotify is not set up")
    api = svc.spotify
    date = concert_date(date)
    deadline = svc.clock.time() + svc.settings.teaser_timeout_s

    def check_deadline() -> None:
        """The cap covers the whole step, so the writes after the paged reads are gated too."""
        if svc.clock.time() > deadline:
            raise ProviderError(
                "spotify-timeout",
                f"Spotify took longer than {svc.settings.teaser_timeout_s} s",
                provider_reply("spotify", svc.operator),
            )

    try:
        a, _ = resolve_artist(artist, api)
        tracks = liked_by_artist(a, api, deadline=deadline)
        name = playlist_name(date, a.name)
        existing = find_playlist_named(api.my_playlists(deadline=deadline), name)
        track_names = tuple(t["name"] for t in tracks)
        if not tracks:
            return TeaserResult("none", a, date, name, 0, tracks=())
        if dry_run:
            log_info(f"dry-run: would {'fill' if existing else 'create'} | name='{name}' tracks='{len(tracks)}'")
            return TeaserResult("dry_run", a, date, name, len(tracks), tracks=track_names, exists=bool(existing))
        check_deadline()
        if existing:
            pl = existing
            log_info(f"teaser exists; topping it up | name='{name}' id='{pl['id']}'")
        else:
            pl = api.create_playlist(
                name=name, description=description or teaser_description(a.name, date), public=False
            )
            log_info(f"teaser created | name='{name}' id='{pl['id']}'")
        have = {r["uri"] for r in api.playlist_items(pl["id"], deadline=deadline)}
        new = new_uris([t["uri"] for t in tracks], have)
        if new:
            check_deadline()
            api.add_items(pl["id"], new)
        url = (pl.get("external_urls") or {}).get("spotify")
        return TeaserResult(
            "updated" if existing else "created",
            a,
            date,
            name,
            len(tracks),
            url=url,
            id=pl["id"],
            added=len(new),
            already_there=len(tracks) - len(new),
            tracks=track_names,
            exists=bool(existing),
        )
    except ProviderError as e:
        status = "timeout" if e.provider == "spotify-timeout" else "failed"
        return TeaserResult(status, None, date, None, 0, reason=str(e.error)[:REASON_LIMIT])
    except NoArtistMatch as e:
        return TeaserResult("failed", None, date, None, 0, reason=str(e)[:REASON_LIMIT])
    except Exception as e:  # noqa: BLE001 - the calendar event must land whatever Spotify does
        return TeaserResult("failed", None, date, None, 0, reason=f"{type(e).__name__}: {e}"[:REASON_LIMIT])


def teaser_outcome(artist: str, date: str, svc: Services, *, dry_run: bool = False) -> Outcome:
    """The standalone teaser_playlist tool's Outcome."""
    try:
        if not artist:
            raise need("artist")
        if not date:
            raise need("date")
        res = make_teaser(artist, date, svc, dry_run=dry_run)
        if res.status == "unavailable":
            return Outcome(
                Kind.ERROR,
                reply=provider_reply("spotify-unconfigured", svc.operator),
                error="spotify not configured",
            )
        if res.status in ("failed", "timeout"):
            return Outcome(Kind.ERROR, reply=f"No teaser: {res.reason}", error=res.reason, data=res.to_json())
        return Outcome(
            Kind.DRY_RUN if dry_run else Kind.OK,
            reply=render_teaser_standalone(res, timeout_s=svc.settings.teaser_timeout_s, dry=dry_run),
            data=res.to_json(),
        )
    except (FixInput, NeedUser, ProviderError) as e:
        return outcome_from(e)
    except Exception as e:  # noqa: BLE001 - a tool never raises
        return crash_outcome(e, svc.operator)
