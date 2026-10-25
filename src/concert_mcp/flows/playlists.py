"""spotify.py's playlist CRUD kept for the CLI (unit flows-music). Each returns the legacy JSON record.

These are developer commands, not tools the model sees: a ProviderError propagates to the CLI, which prints the
message on stderr and exits 1; a FixInput does the same with its FIX line.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from concert_mcp.adapters.protocols import SpotifyApi
from concert_mcp.flows.services import Services
from concert_mcp.log import log_info, log_warn
from concert_mcp.result import FixInput, ProviderError
from concert_mcp.text.artists import new_uris, playlist_row, validate_track_uris
from concert_mcp.text.reply import provider_reply


def require_spotify(svc: Services) -> SpotifyApi:
    """svc.spotify, or ProviderError('spotify-unconfigured')."""
    if svc.spotify is None:
        raise ProviderError(
            "spotify-unconfigured",
            f"not authorized yet | path='{svc.settings.spotify_credentials}' hint='run: concert-mcp spotify auth'",
            provider_reply("spotify-unconfigured", svc.operator),
        )
    return svc.spotify


def me(svc: Services) -> dict[str, Any]:
    """The signed-in user."""
    u = require_spotify(svc).me()
    return {
        "id": u.get("id"),
        "display_name": u.get("display_name"),
        "url": (u.get("external_urls") or {}).get("spotify"),
        "product": u.get("product"),
    }


def liked(svc: Services, *, artist: str | None = None, refresh: bool = False) -> dict[str, Any]:
    """Liked Songs, oldest first, optionally by one artist (exact name first, then a substring on a fresh fetch)."""
    api = require_spotify(svc)
    rows = api.liked_rows(refresh=refresh)
    if artist:
        want = artist.strip().lower()
        rows = [r for r in rows if any((a or "").lower() == want for a in r.get("artists") or [])]
        if not rows:
            rows = [
                r
                for r in api.liked_rows(refresh=True)
                if any(want in (a or "").lower() for a in r.get("artists") or [])
            ]
    return {"artist": artist, "count": len(rows), "tracks": rows}


def list_playlists(svc: Services) -> dict[str, Any]:
    """The user's playlists."""
    rows = [playlist_row(pl) for pl in require_spotify(svc).my_playlists()]
    return {"count": len(rows), "playlists": rows}


def playlist_tracks(playlist_id: str, svc: Services) -> dict[str, Any]:
    """The tracks of one playlist."""
    rows = list(require_spotify(svc).playlist_items(playlist_id))
    return {"playlist": playlist_id, "count": len(rows), "tracks": rows}


def create_playlist(
    name: str,
    svc: Services,
    *,
    description: str = "",
    public: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a playlist; refuses a name that already exists."""
    api = require_spotify(svc)
    name = name.strip()
    if not name:
        raise FixInput("empty playlist name", "pass --name")
    existing = next(
        (pl for pl in api.my_playlists() if (pl.get("name") or "").strip().lower() == name.lower()),
        None,
    )
    if existing:
        log_warn(f"a playlist with that name exists; not creating another | name='{name}' id='{existing.get('id')}'")
        return {"created": False, **playlist_row(existing)}
    if dry_run:
        log_info(f"dry-run: would create | name='{name}' public='{public}'")
        return {"created": False, "dry_run": True, "name": name}
    pl = api.create_playlist(name=name, description=description or "", public=bool(public))
    log_info(f"playlist created | name='{name}' id='{pl.get('id')}'")
    return {"created": True, **playlist_row(pl)}


def add_tracks(playlist_id: str, uris: Sequence[str], svc: Services, *, dry_run: bool = False) -> dict[str, Any]:
    """Add tracks, skipping ones already present (the adapter posts 100 per request)."""
    api = require_spotify(svc)
    wanted = validate_track_uris(uris)
    have = {row.get("uri") for row in api.playlist_items(playlist_id)}
    new = new_uris(wanted, have)
    skipped = len(wanted) - len(new)
    if dry_run:
        log_info(f"dry-run: would add | playlist='{playlist_id}' add='{len(new)}' skipped='{skipped}'")
        return {"added": 0, "would_add": len(new), "skipped": skipped, "dry_run": True}
    if new:
        api.add_items(playlist_id, new)
    log_info(f"tracks added | playlist='{playlist_id}' added='{len(new)}' skipped_existing='{skipped}'")
    return {"added": len(new), "skipped": skipped, "playlist": playlist_id}


def rename_playlist(playlist_id: str, name: str, svc: Services, *, dry_run: bool = False) -> dict[str, Any]:
    """Rename a playlist the user owns; idempotent (an unchanged name is a no-op)."""
    api = require_spotify(svc)
    pl = api.playlist(playlist_id, "id,name,owner(id)")
    new = name.strip()
    if not new:
        raise FixInput("empty name", "pass --name")
    if pl.get("name") == new:
        return {"renamed": False, "id": pl.get("id"), "name": new, "reason": "already that name"}
    owner = (pl.get("owner") or {}).get("id")
    if owner != api.user_id:
        raise ProviderError(
            "spotify",
            f"not the owner; Spotify only lets the owner rename | playlist='{pl.get('name')}' owner='{owner}'",
            provider_reply("spotify", svc.operator),
        )
    if dry_run:
        log_info(f"dry-run: would rename | from='{pl.get('name')}' to='{new}'")
        return {"renamed": False, "dry_run": True, "id": pl.get("id"), "from": pl.get("name"), "to": new}
    api.rename_playlist(playlist_id, new)
    log_info(f"renamed | from='{pl.get('name')}' to='{new}'")
    return {"renamed": True, "id": pl.get("id"), "from": pl.get("name"), "to": new}


def unfollow_playlist(playlist_id: str, svc: Services, *, dry_run: bool = False) -> dict[str, Any]:
    """Remove a playlist from the library (the API cannot delete one; for the owner this is the same thing)."""
    api = require_spotify(svc)
    if dry_run:
        log_info(f"dry-run: would unfollow | playlist='{playlist_id}'")
        return {"unfollowed": False, "dry_run": True, "playlist": playlist_id}
    api.unfollow_playlist(playlist_id)
    log_info(f"playlist unfollowed | playlist='{playlist_id}'")
    return {"unfollowed": True, "playlist": playlist_id}


def search_artist(name: str, svc: Services) -> dict[str, Any]:
    """The best artist matches."""
    items = require_spotify(svc).search_artists(name, 5)
    rows = [
        {
            "name": a.get("name"),
            "id": a.get("id"),
            "url": (a.get("external_urls") or {}).get("spotify"),
            "followers": (a.get("followers") or {}).get("total"),
            "genres": a.get("genres"),
        }
        for a in items
    ]
    return {"query": name, "artists": rows}
