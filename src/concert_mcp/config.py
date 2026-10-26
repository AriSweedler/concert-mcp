"""Settings: CONCERT_MCP_* env vars over <config_dir>/config.toml over defaults. Pure given `env` and `home`."""

from __future__ import annotations

import os
import shutil
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

ENV_PREFIX = "CONCERT_MCP_"

# Field names computed from other fields, never read from the environment.
DERIVED_FIELDS = frozenset({"legacy_in_use"})

# The four paths that fall back to the ari-spotify locations when the new file is absent.
LEGACY_FIELDS = ("spotify_credentials", "setlistfm_credentials", "liked_cache", "setlist_queue")
CREDENTIAL_FIELDS = frozenset({"spotify_credentials", "setlistfm_credentials"})

# Where Homebrew, a package manager and `uv tool install` put binaries; searched when PATH has none, because a
# gateway launched by launchd carries a bare PATH. A `~` prefix is the configured home.
FALLBACK_BIN_DIRS: tuple[str, ...] = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin")


@dataclass(frozen=True)
class Settings:
    config_dir: Path
    state_dir: Path
    cache_dir: Path
    spotify_credentials: Path
    setlistfm_credentials: Path
    liked_cache: Path
    setlist_queue: Path
    candidates_file: Path
    sent_log: Path
    fix_log: Path
    topics_file: Path
    gws_bin: str
    gws_key_file: Path
    gws_timeout_s: int
    openclaw_bin: str
    openclaw_home: Path
    openclaw_config: Path
    session_db: Path
    ocr_last: Path
    tz: str
    concerts_calendar: str
    default_calendar: str
    default_calendar_name: str
    context_window_s: int
    dedupe_window_s: int
    teaser_timeout_s: int
    http_timeout_s: int
    liked_cache_ttl_s: int
    setlist_queue_days: int
    default_duration_h: int
    past_grace_h: int
    max_future_days: int
    spotify_redirect_uri: str
    spotify_scopes: str
    op_vault: str
    op_spotify_item: str
    op_setlistfm_item: str
    venues_file: Path
    places_credentials: Path
    venue_lookup: bool
    op_places_item: str
    operator_name: str
    send: bool
    rename_topics: bool
    dry_run: bool
    log_level: str
    legacy_in_use: frozenset[str]  # field names whose legacy path is the effective one


# One table drives env_table(), the README and `concert-mcp config`: (field, default as documented, meaning).
# Defaults written as text because most depend on another field or on the machine.
FIELD_DOCS: tuple[tuple[str, str, str], ...] = (
    ("config_dir", "~/.config/concert-mcp", "credentials files and config.toml"),
    (
        "state_dir",
        "~/.local/state/concert-mcp",
        "setlist-queue.json, delete-candidates.json, last-sent.json, fix-log.json, topics.json",
    ),
    ("cache_dir", "~/.cache/concert-mcp", "liked.json"),
    ("spotify_credentials", "<config_dir>/spotify.json", "legacy fallback ~/.config/ari-spotify/credentials.json"),
    ("setlistfm_credentials", "<config_dir>/setlistfm.json", "legacy fallback ~/.config/ari-spotify/setlistfm.json"),
    ("liked_cache", "<cache_dir>/liked.json", "legacy fallback ~/.cache/ari-spotify/liked.json"),
    (
        "setlist_queue",
        "<state_dir>/setlist-queue.json",
        "legacy fallback ~/.local/state/ari-spotify/setlist-queue.json; the weekly poller reads this file",
    ),
    ("candidates_file", "<state_dir>/delete-candidates.json", "no legacy fallback (lists are short-lived)"),
    ("sent_log", "<state_dir>/last-sent.json", "reply dedupe memory"),
    ("fix_log", "<state_dir>/fix-log.json", "the retry-loop breaker's memory"),
    ("topics_file", "<state_dir>/topics.json", "which event each Telegram topic owns"),
    (
        "gws_bin",
        "which gws, else /opt/homebrew/bin, /usr/local/bin, ~/.local/bin, else gws",
        "the Google Workspace CLI",
    ),
    (
        "gws_key_file",
        "~/.config/gws/.encryption_key",
        "exists -> GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file on every gws call",
    ),
    ("gws_timeout_s", "60", "wall-clock cap per gws call"),
    (
        "openclaw_bin",
        "which openclaw, else /opt/homebrew/bin, /usr/local/bin, ~/.local/bin, else openclaw",
        "the OpenClaw CLI",
    ),
    ("openclaw_home", "~/.openclaw", "base for openclaw_config, session_db and ocr_last"),
    ("openclaw_config", "<openclaw_home>/openclaw.json", "read for channels.telegram.botToken only"),
    ("session_db", "<openclaw_home>/agents/main/agent/openclaw-agent.sqlite", "opened read-only"),
    ("ocr_last", "<openclaw_home>/media/ocr-last.txt", "the last OCR output"),
    ("tz", "America/Los_Angeles", "IANA zone for every start/end"),
    ("concerts_calendar", "Concerts", "calendar name, resolved through gws calendar calendarList list"),
    ("default_calendar", "primary", "id used for plain events and the second delete search"),
    ("default_calendar_name", "Personal", "how replies name it"),
    ("context_window_s", "900", "live chat / OCR / transcript recency"),
    ("dedupe_window_s", "600", "an identical reply is not sent twice within this window"),
    ("teaser_timeout_s", "150", "cap on the Spotify teaser step"),
    ("http_timeout_s", "30", "per HTTP request"),
    ("liked_cache_ttl_s", "21600", "Liked Songs cache lifetime"),
    ("setlist_queue_days", "61", "how long after the show the weekly setlist check keeps looking"),
    ("default_duration_h", "2", "end = start + this when no end is given"),
    ("past_grace_h", "12", "a start this far in the past is still accepted"),
    ("max_future_days", "730", "a start further out is refused"),
    ("spotify_redirect_uri", "http://127.0.0.1:8888/callback", "the Spotify app's redirect URI"),
    (
        "spotify_scopes",
        "user-library-read playlist-read-private playlist-modify-private playlist-modify-public",
        "OAuth scopes",
    ),
    ("op_vault", "Private", "1Password vault, auth commands only"),
    ("op_spotify_item", "Spotify - concert-mcp", "fields username (client id), credential (secret), refresh token"),
    ("op_setlistfm_item", "setlist.fm - API key", "field credential"),
    ("venues_file", "<config_dir>/venues.json", "the venue registry: address lines, website, dates visited"),
    ("places_credentials", "<config_dir>/places.json", "optional Google Places key; without it Nominatim answers"),
    ("venue_lookup", "1", "0 skips every network venue lookup; the ticket's address block is still used"),
    ("op_places_item", "Google Places - API key", "field credential, `venues auth` only"),
    ("operator_name", "Admin", "the name in 'Admin: check `gws auth` on the host.'"),
    ("send", "1", "0 disables reply delivery and topic renames everywhere"),
    ("rename_topics", "1", "0 keeps Telegram topic names as they are"),
    ("dry_run", "0", "1 makes every tool change nothing and return the reply it would have sent, NOT SENT: dry run"),
    ("log_level", "info", "debug logs every subprocess argv (never tokens); stderr only"),
)

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def env_name(field: str) -> str:
    """CONCERT_MCP_<FIELD>."""
    return ENV_PREFIX + field.upper()


def env_table() -> list[tuple[str, str, str]]:
    """(ENV_NAME, default-as-doc, meaning) for every configurable field, in FIELD_DOCS order."""
    return [(env_name(name), default, meaning) for name, default, meaning in FIELD_DOCS]


def _field_types() -> dict[str, str]:
    return {f.name: str(f.type) for f in fields(Settings)}


def _expand(value: str, home: Path) -> Path:
    v = value.strip()
    if v == "~":
        return home
    if v.startswith("~/"):
        return home / v[2:]
    return Path(v)


def _xdg(env: Mapping[str, str], var: str, home: Path, fallback: str) -> Path:
    raw = env.get(var, "").strip()
    return Path(raw) if raw else home / fallback


def read_config_file(path: Path) -> dict[str, str]:
    """Top-level keys of <config_dir>/config.toml as strings; {} when the file is absent."""
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"{path}: not valid TOML: {e}") from e
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            out[key] = str(value)
        else:
            raise ValueError(f"{path}: {key} must be a string, number or boolean")
    return out


def legacy_paths(home: Path, env: Mapping[str, str]) -> dict[str, Path]:
    """Where the ari-spotify scripts kept the four files this package reads."""
    config = _xdg(env, "XDG_CONFIG_HOME", home, ".config") / "ari-spotify"
    cache = _xdg(env, "XDG_CACHE_HOME", home, ".cache") / "ari-spotify"
    state = _xdg(env, "XDG_STATE_HOME", home, ".local/state") / "ari-spotify"
    return {
        "spotify_credentials": config / "credentials.json",
        "setlistfm_credentials": config / "setlistfm.json",
        "liked_cache": cache / "liked.json",
        "setlist_queue": state / "setlist-queue.json",
    }


def with_legacy_fallback(new: Path, legacy: Path) -> tuple[Path, bool]:
    """(new, False) when new exists; (legacy, True) when only legacy exists; else (new, False)."""
    if new.exists():
        return new, False
    if legacy.exists():
        return legacy, True
    return new, False


def _default_paths(config_dir: Path, state_dir: Path, cache_dir: Path) -> dict[str, Path]:
    return {
        "spotify_credentials": config_dir / "spotify.json",
        "setlistfm_credentials": config_dir / "setlistfm.json",
        "liked_cache": cache_dir / "liked.json",
        "setlist_queue": state_dir / "setlist-queue.json",
    }


def _parse_bool(name: str, value: str) -> bool:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"{env_name(name)} must be one of 1/0, true/false, yes/no, on/off; got {value!r}")


def _parse_int(name: str, value: str) -> int:
    try:
        return int(value.strip())
    except ValueError as e:
        raise ValueError(f"{env_name(name)} must be an integer; got {value!r}") from e


def load_settings(env: Mapping[str, str] | None = None, home: Path | None = None) -> Settings:
    """Build Settings: default < config.toml < env. Pure given `env` and `home`."""
    if env is None:
        env = dict(os.environ)
    if home is None:
        home = Path.home()
    home = Path(home)

    config_dir = (
        _expand(env[env_name("config_dir")], home)
        if env.get(env_name("config_dir"))
        else (_xdg(env, "XDG_CONFIG_HOME", home, ".config") / "concert-mcp")
    )
    toml = read_config_file(config_dir / "config.toml")

    def explicit(name: str) -> str | None:
        """The configured value for a field, env first, then the toml file; None when neither sets it."""
        v = env.get(env_name(name))
        if v is not None and v != "":
            return v
        t = toml.get(name)
        if t is not None and t != "":
            return t
        return None

    def path_of(name: str, default: Path) -> Path:
        v = explicit(name)
        return _expand(v, home) if v is not None else default

    def str_of(name: str, default: str) -> str:
        v = explicit(name)
        return v.strip() if v is not None else default

    def int_of(name: str, default: int) -> int:
        v = explicit(name)
        return _parse_int(name, v) if v is not None else default

    def bool_of(name: str, default: bool) -> bool:
        v = explicit(name)
        return _parse_bool(name, v) if v is not None else default

    def which(name: str, binary: str) -> str:
        """The configured path, else the binary on PATH, else one in FALLBACK_BIN_DIRS, else the bare name."""
        v = explicit(name)
        if v is not None:
            return v.strip()
        found = shutil.which(binary, path=env.get("PATH", ""))
        if found:
            return found
        for d in FALLBACK_BIN_DIRS:
            candidate = _expand(d, home) / binary
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return binary

    state_dir = path_of("state_dir", _xdg(env, "XDG_STATE_HOME", home, ".local/state") / "concert-mcp")
    cache_dir = path_of("cache_dir", _xdg(env, "XDG_CACHE_HOME", home, ".cache") / "concert-mcp")
    openclaw_home = path_of("openclaw_home", home / ".openclaw")

    defaults = _default_paths(config_dir, state_dir, cache_dir)
    legacy = legacy_paths(home, env)
    effective: dict[str, Path] = {}
    legacy_in_use: set[str] = set()
    for name in LEGACY_FIELDS:
        if explicit(name) is not None:
            effective[name] = path_of(name, defaults[name])
            continue
        chosen, is_legacy = with_legacy_fallback(defaults[name], legacy[name])
        effective[name] = chosen
        if is_legacy:
            legacy_in_use.add(name)

    return Settings(
        config_dir=config_dir,
        state_dir=state_dir,
        cache_dir=cache_dir,
        spotify_credentials=effective["spotify_credentials"],
        setlistfm_credentials=effective["setlistfm_credentials"],
        liked_cache=effective["liked_cache"],
        setlist_queue=effective["setlist_queue"],
        candidates_file=path_of("candidates_file", state_dir / "delete-candidates.json"),
        sent_log=path_of("sent_log", state_dir / "last-sent.json"),
        fix_log=path_of("fix_log", state_dir / "fix-log.json"),
        topics_file=path_of("topics_file", state_dir / "topics.json"),
        gws_bin=which("gws_bin", "gws"),
        gws_key_file=path_of("gws_key_file", home / ".config/gws/.encryption_key"),
        gws_timeout_s=int_of("gws_timeout_s", 60),
        openclaw_bin=which("openclaw_bin", "openclaw"),
        openclaw_home=openclaw_home,
        openclaw_config=path_of("openclaw_config", openclaw_home / "openclaw.json"),
        session_db=path_of("session_db", openclaw_home / "agents/main/agent/openclaw-agent.sqlite"),
        ocr_last=path_of("ocr_last", openclaw_home / "media/ocr-last.txt"),
        tz=str_of("tz", "America/Los_Angeles"),
        concerts_calendar=str_of("concerts_calendar", "Concerts"),
        default_calendar=str_of("default_calendar", "primary"),
        default_calendar_name=str_of("default_calendar_name", "Personal"),
        context_window_s=int_of("context_window_s", 900),
        dedupe_window_s=int_of("dedupe_window_s", 600),
        teaser_timeout_s=int_of("teaser_timeout_s", 150),
        http_timeout_s=int_of("http_timeout_s", 30),
        liked_cache_ttl_s=int_of("liked_cache_ttl_s", 21600),
        setlist_queue_days=int_of("setlist_queue_days", 61),
        default_duration_h=int_of("default_duration_h", 2),
        past_grace_h=int_of("past_grace_h", 12),
        max_future_days=int_of("max_future_days", 730),
        spotify_redirect_uri=str_of("spotify_redirect_uri", "http://127.0.0.1:8888/callback"),
        spotify_scopes=str_of(
            "spotify_scopes", "user-library-read playlist-read-private playlist-modify-private playlist-modify-public"
        ),
        op_vault=str_of("op_vault", "Private"),
        op_spotify_item=str_of("op_spotify_item", "Spotify - concert-mcp"),
        op_setlistfm_item=str_of("op_setlistfm_item", "setlist.fm - API key"),
        venues_file=path_of("venues_file", config_dir / "venues.json"),
        places_credentials=path_of("places_credentials", config_dir / "places.json"),
        venue_lookup=bool_of("venue_lookup", True),
        op_places_item=str_of("op_places_item", "Google Places - API key"),
        operator_name=str_of("operator_name", "Admin"),
        send=bool_of("send", True),
        rename_topics=bool_of("rename_topics", True),
        dry_run=bool_of("dry_run", False),
        log_level=str_of("log_level", "info").lower(),
        legacy_in_use=frozenset(legacy_in_use),
    )


def redacted(settings: Settings) -> list[tuple[str, str, str]]:
    """(field, value, EXISTS | MISSING | LEGACY | -) per field, in FIELD_DOCS order. Never reads a file's contents."""
    rows: list[tuple[str, str, str]] = []
    for name, _default, _meaning in FIELD_DOCS:
        value = getattr(settings, name)
        if not isinstance(value, Path):
            status = "-"
        elif name in settings.legacy_in_use:
            status = "LEGACY"
        else:
            status = "EXISTS" if value.exists() else "MISSING"
        rows.append((name, str(value), status))
    return rows


def migrate(
    settings: Settings,
    home: Path,
    *,
    dry: bool = False,
    env: Mapping[str, str] | None = None,
) -> list[tuple[Path, Path, str]]:
    """Copy each legacy ari-spotify file to its concert-mcp path when that path is absent. Never deletes.

    Returns (src, dst, status) per legacy field with status "copied", "skipped: exists", "skipped: no legacy", or
    "would copy" on a dry run. The two credential files are written with mode 600.
    """
    if env is None:
        env = dict(os.environ)
    legacy = legacy_paths(Path(home), env)
    defaults = _default_paths(settings.config_dir, settings.state_dir, settings.cache_dir)
    out: list[tuple[Path, Path, str]] = []
    for name in LEGACY_FIELDS:
        src = legacy[name]
        dst = defaults[name] if name in settings.legacy_in_use else getattr(settings, name)
        if dst == src:
            dst = defaults[name]
        if not src.exists():
            out.append((src, dst, "skipped: no legacy"))
            continue
        if dst.exists():
            out.append((src, dst, "skipped: exists"))
            continue
        if dry:
            out.append((src, dst, "would copy"))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if name in CREDENTIAL_FIELDS:
            dst.chmod(0o600)
        out.append((src, dst, "copied"))
    return out
