"""The command line: the same flows as the server, for developers and the dotfiles wrappers (unit cli).

    concert-mcp [--json] [--dry-run] [--no-send] [--config-dir PATH] [--log-level LEVEL] <subcommand> ...

`add` and `del` keep the legacy input of cal-add.py and cal-del.py: `--key value` flags, one JSON object as the
positional, or one JSON object on stdin. Every other subcommand is plain argparse. Exactly one text block or one JSON
line goes to stdout; logging goes to stderr; a traceback never reaches either. Exit codes are the legacy 0/1, and
`healthcheck` exits with its FAIL count. No subcommand means `serve`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn, TextIO

from concert_mcp.config import Settings, env_name, load_settings, migrate, redacted
from concert_mcp.flows import auth, playlists, services
from concert_mcp.flows.concert_add import ALIASES, KNOWN, AddRequest, add_event
from concert_mcp.flows.deliver import deliver, render_tool_result, run_tool
from concert_mcp.flows.event_delete import DELETE_ALIASES, DELETE_KNOWN, DeleteRequest, delete_event
from concert_mcp.flows.healthcheck import run_healthcheck
from concert_mcp.flows.queue import queue_outcome
from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist import find_setlist, setlist_outcome
from concert_mcp.flows.teaser import make_teaser, teaser_outcome
from concert_mcp.log import log_err, set_level
from concert_mcp.result import Delivery, FixInput, Kind, Outcome, ProviderError, need, outcome_from
from concert_mcp.text.health import render_health
from concert_mcp.text.reply import provider_reply, render_setlist_standalone, render_teaser_standalone

ADD_JSON_HINT = "send exactly one JSON object, keys and strings in double quotes"
DEL_JSON_HINT = 'send exactly one JSON object, e.g. {"title": "Zed Fox", "date": "2026-10-24"}'

# `add` and `del` take their request from the arguments argparse did not recognise.
REQUEST_COMMANDS = frozenset({"add", "del"})

SPOTIFY_COMMANDS = (
    "auth",
    "me",
    "liked",
    "playlists",
    "playlist-tracks",
    "playlist-create",
    "playlist-add",
    "rename",
    "playlist-unfollow",
    "search-artist",
    "setlist-auth",
)


class _Parser(argparse.ArgumentParser):
    """argparse that reports its own errors as FixInput instead of a usage dump and exit 2."""

    def error(self, message: str) -> NoReturn:
        raise FixInput(message, f"{message}; run `{self.prog} --help` for the accepted flags")


def _add_globals(p: argparse.ArgumentParser, *, suppress: bool) -> None:
    """The five global flags. On a subparser the defaults are SUPPRESS so they never shadow the main parser's."""
    flag = argparse.SUPPRESS if suppress else False
    value = argparse.SUPPRESS if suppress else None
    p.add_argument("--json", dest="json_mode", action="store_true", default=flag, help="print the JSON record")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=flag, help="change and send nothing")
    p.add_argument("--no-send", dest="no_send", action="store_true", default=flag, help="never deliver the reply")
    p.add_argument("--config-dir", dest="config_dir", metavar="PATH", default=value, help="CONCERT_MCP_CONFIG_DIR")
    p.add_argument("--log-level", dest="log_level", metavar="LEVEL", default=value, help="debug|info|warn|error|off")


def build_parser() -> argparse.ArgumentParser:
    """concert-mcp [--json] [--dry-run] [--no-send] [--config-dir PATH] [--log-level LEVEL] <subcommand> ..."""
    parser = _Parser(
        prog="concert-mcp",
        description="Concert tools: Google Calendar, Spotify teaser, setlist.fm. No subcommand runs `serve`.",
        allow_abbrev=False,
    )
    _add_globals(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", metavar="<subcommand>")

    def add(name: str, help_text: str, **kw: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text, allow_abbrev=False, **kw)
        _add_globals(p, suppress=True)
        return p

    add("serve", "the MCP stdio server (what OpenClaw launches)")

    p = add(
        "add",
        "add one event: --title --start --location [--end --tz --calendar --description* --attendees* --source "
        "--allowPast] (aliases --artist --summary --datetime --timezone --venue --notes --text), "
        "or one JSON object as the argument or on stdin",
    )
    p.add_argument("--concert", action="store_true", help="the Concerts calendar, teaser playlist, setlist lookup")
    p.add_argument("--no-teaser", dest="no_teaser", action="store_true", help="skip the teaser playlist")

    add(
        "del",
        "delete one event: --title [--date --calendar --id --pick] (aliases --artist --summary --event --name "
        "--venue --when --day --cal --eventId --number --choice), or one JSON object as the argument or on stdin",
        aliases=["delete"],
    )

    p = add("teaser", "make or top up the Spotify teaser playlist for one concert")
    p.add_argument("--artist", required=True)
    p.add_argument("--date", required=True, help="concert date, YYYY-MM-DD")
    p.add_argument("--description", default="", help="override the playlist description")

    p = add("setlist-find", "look up one show on setlist.fm")
    p.add_argument("--artist", required=True)
    p.add_argument("--date", required=True, help="concert date, YYYY-MM-DD")
    p.add_argument("--venue", default="", help="venue name, used to break ties")
    p.add_argument("--songs", action="store_true", help="include the song list in the --json record")

    add("queue", "the concerts waiting for the weekly setlist check")

    p = add("healthcheck", "which credentials and tools are present; exit code = number of FAIL rows")
    p.add_argument("--probe", action="store_true", help="make one real call per provider")
    p.add_argument("--only", action="append", default=[], metavar="NAME", help="run only this check (repeatable)")

    add("config", "the effective settings, redacted, with EXISTS/MISSING/LEGACY per path")
    add("migrate", "copy the legacy ari-spotify files into the concert-mcp dirs (never deletes)")

    sp = add("spotify", "Spotify and setlist.fm maintenance commands (JSON out)")
    ssub = sp.add_subparsers(dest="spotify_command", metavar="<command>", required=True)

    def sadd(name: str, help_text: str) -> argparse.ArgumentParser:
        q = ssub.add_parser(name, help=help_text, description=help_text, allow_abbrev=False)
        _add_globals(q, suppress=True)
        return q

    q = sadd("auth", "one-time browser login; writes the credentials file")
    q.add_argument("--force", action="store_true", help="log in again even if credentials exist")
    q.add_argument("--manual", action="store_true", help="type the client id and secret instead of reading 1Password")
    sadd("me", "the signed-in user")
    q = sadd("liked", "Liked Songs, oldest first")
    q.add_argument("--artist", help="keep only tracks by this artist")
    q.add_argument("--refresh", action="store_true", help="ignore the cache")
    sadd("playlists", "the user's playlists")
    q = sadd("playlist-tracks", "the tracks of one playlist")
    q.add_argument("--playlist", required=True, help="playlist id")
    q = sadd("playlist-create", "create a private playlist; refuses a name that exists")
    q.add_argument("--name", required=True)
    q.add_argument("--description", default="")
    q.add_argument("--public", action="store_true")
    q = sadd("playlist-add", "add tracks to a playlist, skipping ones already in it")
    q.add_argument("--playlist", required=True, help="playlist id")
    q.add_argument("--uri", action="append", required=True, help="spotify:track:<id>, repeatable")
    q = sadd("rename", "rename a playlist you own")
    q.add_argument("--playlist", required=True, help="playlist id")
    q.add_argument("--name", required=True)
    q = sadd("playlist-unfollow", "remove a playlist from the library")
    q.add_argument("--playlist", required=True, help="playlist id")
    q = sadd("search-artist", "the best artist matches")
    q.add_argument("--name", required=True)
    q = sadd("setlist-auth", "store the setlist.fm API key, once")
    q.add_argument("--force", action="store_true")
    q.add_argument("--manual", action="store_true", help="type the key instead of reading 1Password")
    return parser


def read_request(
    args: Sequence[str],
    stdin: TextIO,
    *,
    known: frozenset[str],
    aliases: Mapping[str, str],
    list_keys: frozenset[str] = frozenset({"description", "attendees"}),
    json_hint: str,
    hint_hidden: frozenset[str] = frozenset({"date", "time"}),
) -> dict[str, Any]:
    """The legacy parse: `--key value` flags, or one JSON object from the positional or stdin.

    Flags are checked against `known` and `aliases` and aliases are folded as they are read; `list_keys` repeat.
    `hint_hidden` names the known keys the allowed-flags hint leaves out (cal-add.py hid the date/time pair).
    JSON keys are returned as given: the request's from_mapping folds aliases and refuses unknown keys.
    """
    ev: dict[str, Any] = {}
    positional: list[str] = []
    rest = list(args)
    i = 0
    while i < len(rest):
        a = rest[i]
        if a.startswith("--"):
            key = a[2:]
            if key not in known and key not in aliases:
                allowed = ", ".join("--" + k for k in sorted(known - hint_hidden))
                raise FixInput(f"unknown flag --{key}", f"allowed flags: {allowed}")
            if i + 1 >= len(rest):
                raise FixInput(f"--{key} needs a value", f"pass a value after --{key}")
            val = rest[i + 1]
            key = aliases.get(key, key)
            if key in list_keys:
                ev.setdefault(key, []).append(val)
            else:
                ev[key] = val
            i += 2
        else:
            positional.append(a)
            i += 1
    if not ev:
        raw = positional[0] if positional else stdin.read()
        try:
            ev = json.loads(raw)
        except (ValueError, TypeError) as e:
            raise FixInput(f"input is not valid JSON: {e}", json_hint) from e
    if not isinstance(ev, dict):
        raise FixInput("input must be a JSON object", "send one {...} object")
    return ev


def emit(outcome: Outcome, delivery: Delivery, text: str, *, json_mode: bool) -> int:
    """Print the record (with the delivery status under "sent") or the contract text; return the exit code."""
    if json_mode:
        print(json.dumps({**outcome.to_json(), "sent": delivery.status}, ensure_ascii=False))
    else:
        print(text)
    return outcome.exit_code


def _fix_result(e: FixInput, svc: Services) -> tuple[Outcome, Delivery, str]:
    """A FixInput raised before the flow ran still goes through deliver and render, like every other outcome."""
    outcome = outcome_from(e)
    delivery = deliver(outcome, svc, send=False)
    return outcome, delivery, render_tool_result(outcome, delivery)


def _teaser_outcome(artist: str, date: str, description: str, svc: Services, *, dry_run: bool) -> Outcome:
    """teaser_outcome, or the same shape with the caller's playlist description when one is given."""
    if not description:
        return teaser_outcome(artist, date, svc, dry_run=dry_run)
    if not artist.strip():
        return outcome_from(need("artist"))
    if not date.strip():
        return outcome_from(need("date"))
    try:
        res = make_teaser(artist, date, svc, dry_run=dry_run, description=description)
    except FixInput as e:
        return outcome_from(e)
    if res.status == "unavailable":
        reply = provider_reply("spotify-unconfigured", svc.operator)
        return Outcome(Kind.ERROR, reply=reply, error=res.reason or "Spotify is not configured")
    kind = Kind.DRY_RUN if res.status == "dry_run" else Kind.OK
    text = render_teaser_standalone(res, timeout_s=svc.settings.teaser_timeout_s, dry=dry_run)
    return Outcome(kind, reply=text, data=res.to_json())


def _setlist_outcome(artist: str, date: str, venue: str, svc: Services, *, songs: bool) -> Outcome:
    """setlist_outcome, or the same shape with the song list in the record when --songs is given."""
    if not songs:
        return setlist_outcome(artist, date, venue, svc)
    if not artist.strip():
        return outcome_from(need("artist"))
    if not date.strip():
        return outcome_from(need("date"))
    try:
        res = find_setlist(artist, date, venue or None, svc)
    except FixInput as e:
        return outcome_from(e)
    if res.match == "unavailable":
        reply = provider_reply("setlistfm-unconfigured", svc.operator)
        return Outcome(Kind.ERROR, reply=reply, error=res.reason or "setlist.fm is not configured")
    return Outcome(Kind.OK, reply=render_setlist_standalone(res), data=res.to_json(songs=True))


def _cmd_add(args: argparse.Namespace, extras: list[str], settings: Settings, stdin: TextIO) -> int:
    svc = services.build_services(settings)
    try:
        raw = read_request(extras, stdin, known=KNOWN, aliases=ALIASES, json_hint=ADD_JSON_HINT)
        req = AddRequest.from_mapping(raw, concert=args.concert, teaser=not args.no_teaser, dry_run=args.dry_run)
    except FixInput as e:
        outcome, delivery, text = _fix_result(e, svc)
    else:
        outcome, delivery, text = run_tool(lambda: add_event(req, svc), svc, send=not args.no_send)
    return emit(outcome, delivery, text, json_mode=args.json_mode)


def _cmd_del(args: argparse.Namespace, extras: list[str], settings: Settings, stdin: TextIO) -> int:
    svc = services.build_services(settings)
    try:
        raw = read_request(
            extras,
            stdin,
            known=DELETE_KNOWN,
            aliases=DELETE_ALIASES,
            list_keys=frozenset(),
            json_hint=DEL_JSON_HINT,
            hint_hidden=frozenset(),
        )
        req = DeleteRequest.from_mapping(raw, dry_run=args.dry_run)
    except FixInput as e:
        outcome, delivery, text = _fix_result(e, svc)
    else:
        outcome, delivery, text = run_tool(lambda: delete_event(req, svc), svc, send=not args.no_send)
    return emit(outcome, delivery, text, json_mode=args.json_mode)


def _cmd_teaser(args: argparse.Namespace, settings: Settings) -> int:
    svc = services.build_services(settings)
    outcome, delivery, text = run_tool(
        lambda: _teaser_outcome(args.artist, args.date, args.description, svc, dry_run=args.dry_run),
        svc,
        send=not args.no_send,
    )
    return emit(outcome, delivery, text, json_mode=args.json_mode)


def _cmd_setlist_find(args: argparse.Namespace, settings: Settings) -> int:
    svc = services.build_services(settings)
    outcome, delivery, text = run_tool(
        lambda: _setlist_outcome(args.artist, args.date, args.venue, svc, songs=args.songs),
        svc,
        send=not args.no_send,
    )
    return emit(outcome, delivery, text, json_mode=args.json_mode)


def _cmd_queue(args: argparse.Namespace, settings: Settings) -> int:
    svc = services.build_services(settings)
    outcome, delivery, text = run_tool(lambda: queue_outcome(svc), svc, send=not args.no_send)
    return emit(outcome, delivery, text, json_mode=args.json_mode)


def _cmd_healthcheck(args: argparse.Namespace, settings: Settings) -> int:
    svc = services.build_services(settings) if args.probe else None
    report = run_healthcheck(settings, probe=args.probe, svc=svc, only=tuple(args.only))
    if args.json_mode:
        print(json.dumps(report.to_json(), ensure_ascii=False))
    else:
        print(render_health(report))
    return report.failures


def _cmd_config(args: argparse.Namespace, settings: Settings) -> int:
    rows = redacted(settings)
    if args.json_mode:
        print(json.dumps([{"field": f, "value": v, "status": s} for f, v, s in rows], ensure_ascii=False))
    else:
        print("\n".join(f"{field:<24} {status:<8} {value}" for field, value, status in rows))
    return 0


def _cmd_migrate(args: argparse.Namespace, settings: Settings, env: Mapping[str, str]) -> int:
    rows = migrate(settings, Path.home(), dry=args.dry_run, env=env)
    if args.json_mode:
        print(json.dumps([{"src": str(s), "dst": str(d), "status": st} for s, d, st in rows], ensure_ascii=False))
    else:
        print("\n".join(f"{status:<20} {src} -> {dst}" for src, dst, status in rows))
    return 0


def _spotify_record(args: argparse.Namespace, svc: Services, settings: Settings) -> dict[str, Any]:
    cmd = args.spotify_command
    if cmd == "auth":
        return auth.spotify_auth(settings, svc.state, manual=args.manual, force=args.force)
    if cmd == "me":
        return playlists.me(svc)
    if cmd == "liked":
        return playlists.liked(svc, artist=args.artist, refresh=args.refresh)
    if cmd == "playlists":
        return playlists.list_playlists(svc)
    if cmd == "playlist-tracks":
        return playlists.playlist_tracks(args.playlist, svc)
    if cmd == "playlist-create":
        return playlists.create_playlist(
            args.name, svc, description=args.description, public=args.public, dry_run=args.dry_run
        )
    if cmd == "playlist-add":
        return playlists.add_tracks(args.playlist, args.uri, svc, dry_run=args.dry_run)
    if cmd == "rename":
        return playlists.rename_playlist(args.playlist, args.name, svc, dry_run=args.dry_run)
    if cmd == "playlist-unfollow":
        return playlists.unfollow_playlist(args.playlist, svc, dry_run=args.dry_run)
    if cmd == "search-artist":
        return playlists.search_artist(args.name, svc)
    if cmd == "setlist-auth":
        return auth.setlistfm_auth(settings, svc.state, manual=args.manual, force=args.force)
    raise FixInput(f"unknown spotify command {cmd!r}", f"use one of: {', '.join(SPOTIFY_COMMANDS)}")


def _cmd_spotify(args: argparse.Namespace, settings: Settings) -> int:
    svc = services.build_services(settings)
    try:
        record = _spotify_record(args, svc, settings)
    except ProviderError as e:
        log_err(e.error)
        return 1
    print(json.dumps(record, ensure_ascii=False))
    return 0


def _print_fix(e: FixInput, *, json_mode: bool) -> int:
    """A FixInput with no services to deliver through (argparse errors, spotify commands)."""
    if json_mode:
        print(json.dumps(outcome_from(e).to_json(), ensure_ascii=False))
    else:
        print("FIX: " + e.fix)
    return 1


def _dispatch(argv: list[str], stdin: TextIO) -> int:
    args, extras = build_parser().parse_known_args(argv)
    command = {None: "serve", "delete": "del"}.get(args.command, args.command)
    if command not in REQUEST_COMMANDS and extras:
        joined = " ".join(extras)
        raise FixInput(
            f"unrecognized arguments: {joined}",
            f"remove {joined}; run `concert-mcp {command} --help` for the accepted flags",
        )

    env = dict(os.environ)
    if args.config_dir:
        env[env_name("config_dir")] = args.config_dir
    if args.log_level:
        env[env_name("log_level")] = args.log_level

    if command == "serve":
        os.environ.update({k: env[k] for k in (env_name("config_dir"), env_name("log_level")) if k in env})
        from concert_mcp import server  # lazy: the CLI works without the mcp SDK installed

        server.main()
        return 0

    settings = load_settings(env)
    set_level(settings.log_level)
    if command == "add":
        return _cmd_add(args, extras, settings, stdin)
    if command == "del":
        return _cmd_del(args, extras, settings, stdin)
    if command == "teaser":
        return _cmd_teaser(args, settings)
    if command == "setlist-find":
        return _cmd_setlist_find(args, settings)
    if command == "queue":
        return _cmd_queue(args, settings)
    if command == "healthcheck":
        return _cmd_healthcheck(args, settings)
    if command == "config":
        return _cmd_config(args, settings)
    if command == "migrate":
        return _cmd_migrate(args, settings, env)
    if command == "spotify":
        return _cmd_spotify(args, settings)
    raise FixInput(f"unknown subcommand {command!r}", "run `concert-mcp --help` for the subcommands")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch a subcommand; prints one block on stdout, logs on stderr, never a traceback."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        return _dispatch(args, sys.stdin)
    except FixInput as e:
        return _print_fix(e, json_mode="--json" in args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # the contract: a message on stderr, exit 1, no traceback
        log_err(f"{type(e).__name__}: {e}")
        return 1
