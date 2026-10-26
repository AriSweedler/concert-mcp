"""Interactive one-time auth for Spotify and setlist.fm (unit flows-music). CLI only; may print to stdout.

Both commands copy a secret from 1Password (or the terminal with --manual) into the config dir, mode 600, so every
later run works from the file without touching 1Password. Nothing here is reachable from the MCP server.
"""

from __future__ import annotations

import base64
import getpass
import http.server
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from typing import Any

from concert_mcp.adapters.clock import SystemClock
from concert_mcp.adapters.onepassword import OP_PLACEHOLDER, op_available, op_read, op_store_field
from concert_mcp.adapters.places_http import PROBE_VENUE, GooglePlacesClient, save_places_key
from concert_mcp.adapters.protocols import Clock, StateStore
from concert_mcp.adapters.setlistfm_http import SetlistFmClient, save_api_key
from concert_mcp.adapters.spotify_http import ACCOUNTS, SpotifyClient, SpotifyCredentials
from concert_mcp.config import Settings
from concert_mcp.log import log_info, log_warn
from concert_mcp.proc import Runner, run
from concert_mcp.result import ProviderError
from concert_mcp.text.reply import provider_reply

CALLBACK_TIMEOUT_S = 300
SPOTIFY_ID_LEN = 32
SETLISTFM_KEY_MIN_LEN = 20
PROBE_ARTIST = "Radiohead"

# Indirections so a test can drive the --manual path without a terminal.
_getpass: Callable[[str], str] = getpass.getpass


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


# ---------- Spotify ----------


def _app_credentials_from_op(settings: Settings, runner: Runner) -> tuple[str, str]:
    if not op_available():
        raise ProviderError("op", "missing prerequisite | missing='op' hint='or pass --manual'", "")
    log_info(
        "reading the app's client id and secret from 1Password | "
        f"item='{settings.op_spotify_item}' vault='{settings.op_vault}' "
        "hint='approve the 1Password dialog; --manual types them instead'"
    )
    client_id = op_read(settings.op_vault, settings.op_spotify_item, "username", runner).strip()
    client_secret = op_read(settings.op_vault, settings.op_spotify_item, "credential", runner).strip()
    if OP_PLACEHOLDER in (client_id, client_secret) or not client_id or not client_secret:
        raise ProviderError(
            "op",
            f"the 1Password item still holds the placeholder | item='{settings.op_spotify_item}' "
            "fields='username, credential' hint='paste the Client ID and Client Secret from the Spotify dashboard'",
            "",
        )
    return client_id, client_secret


def _app_credentials_from_terminal() -> tuple[str, str]:
    """Typed into the terminal, hidden; for when 1Password's CLI integration is not answering."""
    if not _stdin_is_tty():
        raise ProviderError("spotify", "--manual needs a terminal to type into | stdin='not a tty'", "")
    client_id = _getpass("Spotify Client ID (hidden): ").strip()
    client_secret = _getpass("Spotify Client Secret (hidden): ").strip()
    if len(client_id) != SPOTIFY_ID_LEN or len(client_secret) != SPOTIFY_ID_LEN:
        raise ProviderError(
            "spotify",
            f"Spotify ids are {SPOTIFY_ID_LEN} hex characters | "
            f"client_id_len='{len(client_id)}' client_secret_len='{len(client_secret)}'",
            "",
        )
    return client_id, client_secret


def _token_request(
    client_id: str,
    client_secret: str,
    form: dict[str, str],
    *,
    urlopen: Callable[..., Any],
    timeout_s: float,
    operator: str,
) -> dict[str, Any]:
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(f"{ACCOUNTS}/api/token", data=data, method="POST")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise ProviderError(
            "spotify",
            f"token request refused | grant='{form.get('grant_type')}' status='{e.code}' body='{body}'",
            provider_reply("spotify", operator),
        ) from e
    except urllib.error.URLError as e:
        raise ProviderError(
            "spotify", f"token request failed | reason='{e.reason}'", provider_reply("spotify", operator)
        ) from e


def _wait_for_code(auth_url: str, redirect_uri: str, state_token: str, open_browser: Callable[[str], Any]) -> str:
    """Serve the redirect URI once and return the authorization code it carries."""
    result: dict[str, str | None] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the http.server hook name
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state_token and "code" in q
            result["code"] = q["code"][0] if ok else None
            result["error"] = None if ok else q.get("error", ["state mismatch"])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            text = "Spotify linked. You can close this tab." if ok else f"Login failed: {result['error']}"
            self.wfile.write(text.encode("utf-8"))

        def log_message(self, *_: object) -> None:
            return

    parsed = urllib.parse.urlparse(redirect_uri)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
    try:
        server = http.server.HTTPServer((host, port), Handler)
    except OSError as e:
        raise ProviderError("spotify", f"cannot listen for the callback | addr='{host}:{port}' reason='{e}'", "") from e
    server.timeout = CALLBACK_TIMEOUT_S
    try:
        log_info(f"opening the Spotify consent page; log in as the account that owns the playlists | url='{auth_url}'")
        print(auth_url, file=sys.stderr)
        open_browser(auth_url)
        server.handle_request()
    finally:
        server.server_close()
    code = result.get("code")
    if not code:
        error = result.get("error") or f"timeout after {CALLBACK_TIMEOUT_S} s"
        raise ProviderError("spotify", f"no authorization code received | error='{error}'", "")
    return code


def spotify_auth(
    settings: Settings,
    state: StateStore,
    *,
    manual: bool = False,
    force: bool = False,
    open_browser: Callable[[str], Any] = webbrowser.open,
    runner: Runner = run,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Browser login; writes the credentials file (600) and stores the refresh token in 1Password, best effort."""
    path = settings.spotify_credentials
    if path.exists() and not force:
        raise ProviderError(
            "spotify", f"credentials already exist | path='{path}' hint='pass --force to log in again'", ""
        )
    client_id, client_secret = (
        _app_credentials_from_terminal() if manual else _app_credentials_from_op(settings, runner)
    )

    state_token = secrets.token_urlsafe(16)
    auth_url = f"{ACCOUNTS}/authorize?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": settings.spotify_redirect_uri,
            "scope": settings.spotify_scopes,
            "state": state_token,
            "show_dialog": "true" if force else "false",
        }
    )
    code = _wait_for_code(auth_url, settings.spotify_redirect_uri, state_token, open_browser)

    tok = _token_request(
        client_id,
        client_secret,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": settings.spotify_redirect_uri},
        urlopen=urlopen,
        timeout_s=settings.http_timeout_s,
        operator=settings.operator_name,
    )
    if not tok.get("refresh_token"):
        raise ProviderError("spotify", f"token response carries no refresh token | keys='{', '.join(sorted(tok))}'", "")
    clk = clock or SystemClock()
    creds = SpotifyCredentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=tok["refresh_token"],
        access_token=tok.get("access_token"),
        expires_at=clk.time() + int(tok.get("expires_in", 3600)),
        scope=tok.get("scope", settings.spotify_scopes),
    )
    creds.save(path, state)

    client = SpotifyClient(settings, clk, state, urlopen=urlopen, creds=creds)
    me = client.me()
    creds.user_id = me["id"]
    creds.save(path, state)
    log_info(f"credentials written | path='{path}' user='{me.get('display_name')}' id='{me['id']}'")

    _store_refresh_token(settings, tok["refresh_token"], runner)
    return {
        "ok": True,
        "user": me.get("display_name"),
        "user_id": me["id"],
        "scope": creds.scope,
        "credentials": str(path),
    }


def _store_refresh_token(settings: Settings, refresh_token: str, runner: Runner) -> None:
    """Best effort: the credentials file is the runtime copy; 1Password is the backup."""
    if not op_available():
        log_warn("op not installed; refresh token kept only in the credentials file")
        return
    log_info(
        f"storing the refresh token in 1Password | item='{settings.op_spotify_item}' hint='approve the dialog; 60 s'"
    )
    if not op_store_field(settings.op_vault, settings.op_spotify_item, "refresh token", refresh_token, runner):
        log_warn("could not store the refresh token in 1Password; it is in the credentials file")


# ---------- setlist.fm ----------


def setlistfm_auth(
    settings: Settings,
    state: StateStore,
    *,
    manual: bool = False,
    force: bool = False,
    runner: Runner = run,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Copy the setlist.fm key from 1Password (or the terminal) into the config dir and probe one search."""
    path = settings.setlistfm_credentials
    if path.exists() and not force:
        raise ProviderError(
            "setlistfm", f"setlist.fm key already stored | path='{path}' hint='pass --force to replace it'", ""
        )
    if manual:
        if not _stdin_is_tty():
            raise ProviderError("setlistfm", "--manual needs a terminal to type into | stdin='not a tty'", "")
        key = _getpass("setlist.fm API key (hidden): ").strip()
    else:
        if not op_available():
            raise ProviderError("op", "missing prerequisite | missing='op' hint='or pass --manual'", "")
        log_info(f"reading the key from 1Password | item='{settings.op_setlistfm_item}' vault='{settings.op_vault}'")
        key = op_read(settings.op_vault, settings.op_setlistfm_item, "credential", runner).strip()
    if not key or key == OP_PLACEHOLDER or len(key) < SETLISTFM_KEY_MIN_LEN:
        raise ProviderError(
            "setlistfm",
            f"that is not a setlist.fm key | length='{len(key)}' "
            "hint='paste the key from https://www.setlist.fm/settings/api into the 1Password item'",
            "",
        )
    save_api_key(path, key, state)
    client = SetlistFmClient(settings, clock or SystemClock(), urlopen=urlopen, api_key=key)
    probe = client.search_setlists(PROBE_ARTIST, page=1)
    return {"ok": True, "config": str(path), "probe_total": (probe or {}).get("total", 0)}


PLACES_KEY_MIN_LEN = 30


def places_auth(
    settings: Settings,
    state: StateStore,
    *,
    manual: bool = False,
    force: bool = False,
    runner: Runner = run,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Copy the Google Places key from 1Password (or the terminal) into the config dir and probe one search."""
    path = settings.places_credentials
    if path.exists() and not force:
        raise ProviderError(
            "places", f"Places key already stored | path='{path}' hint='pass --force to replace it'", ""
        )
    if manual:
        if not _stdin_is_tty():
            raise ProviderError("places", "--manual needs a terminal to type into | stdin='not a tty'", "")
        key = _getpass("Google Places API key (hidden): ").strip()
    else:
        if not op_available():
            raise ProviderError("op", "missing prerequisite | missing='op' hint='or pass --manual'", "")
        log_info(f"reading the key from 1Password | item='{settings.op_places_item}' vault='{settings.op_vault}'")
        key = op_read(settings.op_vault, settings.op_places_item, "credential", runner).strip()
    if not key or key == OP_PLACEHOLDER or len(key) < PLACES_KEY_MIN_LEN:
        raise ProviderError(
            "places",
            f"that is not a Places API key | length='{len(key)}' "
            "hint='create one in Google Cloud with the Places API (New) enabled and paste it'",
            "",
        )
    save_places_key(path, key, state)
    client = GooglePlacesClient(settings, urlopen=urlopen, api_key=key)
    probe = client.find_venue(PROBE_VENUE)
    return {"ok": True, "config": str(path), "probe": probe.name if probe else None}
