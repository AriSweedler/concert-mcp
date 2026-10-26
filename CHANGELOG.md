# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A venue registry (`<config_dir>/venues.json`): every concert's venue is looked up once (Google Places when a key is stored, else OpenStreetMap's Nominatim, else the address block printed on the ticket) and kept with its Maps-form address, website, coordinates and the dates visited. The event's `location` becomes the form Google Calendar turns into a place card, the description gains `Venue: <website>` when known, and a second show at the same venue needs no lookup. `concert-mcp venues`, `venues lookup --name`, `venues auth`; the `places-key` healthcheck row is `SKIP` without a key; `CONCERT_MCP_VENUE_LOOKUP=0` turns the network lookups off.
- A `Headliner: <title>` description line before the `Opener:` lines when a concert has openers.
- One event per Telegram topic (`<state_dir>/topics.json`): a repeat `concert_add` in the topic that already owns an event updates it in place (title, time, venue, description; links the new text lacks are kept) and replies `**Updated on Concerts**` with what changed, so a wrong title is fixed by replying with the right one. `another=yes` (CLI `--another`) adds a second event; `event_delete` frees the topic. `Calendar.patch_event` replaces `patch_description`.

### Fixed

- The `Purchased:` line takes the amount after a receipt's `TOTAL` label (not `SUBTOTAL`), else the largest amount; the count also reads `1 General Admission`, `Qty: 2` and `2 x $20.00`.
- `concert_add` asks the user for the headliner when the artist is a ticket platform (`DICE`, `Ticketweb`, ...) or the venue, and drops a caller-named opener the source text does not contain.

## [0.1.0] - 2026-10-25

The first release extracts the concert stack from three dotfiles scripts (`cal-add.py`, `cal-del.py`, `spotify.py`) into one package. Every reply string, FIX text, record key, path default and guard those scripts implemented is carried over; the deliberate wording changes are listed under Changed and mirror `docs/DESIGN.md` §10.

### Added

- The `concert_mcp` package: `text/` (pure parsing and rendering), `adapters/` (gws, Spotify, setlist.fm, OpenClaw, 1Password, state, clock behind Protocols), `flows/` (the policy, returning `Outcome`), `server.py` and `cli.py`.
- The seven MCP tools over stdio: `concert_add`, `calendar_add`, `event_delete` (with a `pick` parameter), `teaser_playlist`, `setlist_find`, `queue_status`, `healthcheck`. Every parameter is a `Param`: a string with a default of `""` whose validator cleans a number, a null or a quoted value before pydantic sees it, so the model never gets a schema error; the server's instructions carry the NO_REPLY / FIX rules; `event_delete` is annotated destructive and the three lookups read-only. The documented OpenClaw entry filters the model's view to `concert_add`, `calendar_add` and `event_delete`.
- The `concert-mcp` CLI with the same flows: `serve`, `add`, `del` (alias `delete`), `teaser`, `setlist-find`, `queue`, `healthcheck`, `config`, `migrate` and the `spotify` subcommands (`auth`, `me`, `liked`, `playlists`, `playlist-tracks`, `playlist-create`, `playlist-add`, `rename`, `playlist-unfollow`, `search-artist`, `setlist-auth`); global `--json`, `--dry-run`, `--no-send`, `--config-dir`, `--log-level`.
- `concert-mcp healthcheck`: one `PASS/FAIL/SKIP` row per credential and tool (17 checks), a README anchor per failure, exit code equal to the number of failures, `--probe` for one real call per provider, `--only NAME` to run a subset. It is read-only (an absent directory under a writable parent is `PASS ... absent (created on first write)`); a locked-keychain failure of `gws-auth` prescribes the file keyring key, not a login, and `concerts-calendar` is then `SKIP gws-auth failed`.
- `concert-mcp migrate`: copies the four `ari-spotify` files to the `concert-mcp` paths, never deletes, mode 600 for credentials, `--dry-run` reports `would copy`. The legacy paths are read as a fallback until the new files exist.
- `fix_guard`: the third `FIX:` for one tool within 15 minutes becomes a reply asking the user for the details in one line, worded per tool for `concert_add`, `calendar_add` and `event_delete`.
- A date-only `start` is accepted for a concert when the ticket text states the show time (`Start taken from the text (8:30 PM).`).
- A bare number as the `title` of `event_delete` means `pick`, when a candidates list exists. A list older than `CONCERT_MCP_CONTEXT_WINDOW_S` (15 minutes) is removed and answered with `I do not have a list to pick from any more ...`, for `pick` and the bare number alike.
- `clean_param`: tool arguments are squashed, one pair of matching quotes is stripped, and `null`, `None`, `n/a` and `undefined` become empty.
- Two no-space time formats, `2026-11-14 8:30PM` and `2026-11-14 8PM`, beside the six legacy formats.
- Settings from `CONCERT_MCP_*` environment variables over `<config_dir>/config.toml` over defaults, XDG-aware; `concert-mcp config` prints the effective, redacted table. `gws` and `openclaw` are searched on PATH, then in `/opt/homebrew/bin`, `/usr/local/bin` and `~/.local/bin` (a launchd-started gateway has a bare PATH); the healthcheck's `*-binary` rows name `CONCERT_MCP_GWS_BIN` / `CONCERT_MCP_OPENCLAW_BIN` when both miss.
- The server reads the Spotify and setlist.fm credential files lazily, so `concert-mcp spotify auth` or `setlist-auth` run while the gateway is up takes effect on the next call without a restart.
- The Spotify teaser cap (`CONCERT_MCP_TEASER_TIMEOUT_S`) covers the whole step: every paged read carries the deadline and the playlist create and add are skipped past it (`not made (Spotify took longer than 150 s)`).
- `--log-level debug` logs every subprocess argv with a secret argument shown as `<redacted>`; `op item edit ... "refresh token[password]=..."` is logged without the token. Credential files are created with mode 600 from the start, never 644 and then chmod-ed.
- A crash inside a tool becomes the reply `Something broke inside the concert tool (<Type>). <operator>: check the gateway log.` instead of an MCP error.
- Tests on fakes for every layer, a stdio smoke test, a docs test (`tests/test_readme.py`, which also scans every text file in the repository for personal data: emails, Telegram chat ids and bot tokens, Google calendar ids, `.local` hostnames) and a grep that keeps `print()` out of the library. Every example and fixture is fictional (`Zed Fox`, `The Quarry`, `ZZZ Bistro`).
- `docs/DESIGN.md`, `docs/migration.md`, `docs/homelab-integration.md`, `CONTRIBUTING.md`, a bug-report template, `.python-version` (3.12), GitHub Actions CI (lint, tests on 3.11 to 3.13, a bare-box healthcheck, the personal-data scan as its own job).

### Changed

- `Ari:` → `{operator}:` (`CONCERT_MCP_OPERATOR_NAME`, default `Admin`); `on the laptop` → `on the host`; `The laptop keychain` → `The login keychain`; the locked-keychain reply ends `{operator}: create the gws file keyring key (README.md#google-calendar) or run this from a session with keychain access.` instead of naming a tmux pane.
- The inferred-show-time FIX is literal: `set start to "2026-11-14 8:30 PM" (the show time in the text; 7:30 PM is doors)` instead of `the text puts the show at 8:30 PM (doors are earlier); use that`, so the model copies a value rather than composing one.
- A plain (`calendar_add`) event no longer runs the ticket parsers over the live context: no show-time check, no derived Doors/Purchased/Opener lines, and a title such as `[Dinner] Team dinner with Sam` is kept whole instead of being split at `with`. The hallucination filter still drops a caller line whose URL, price or clock time is not in the text. (The legacy ran every parser for every event, but its DM flow never shared a context window with a ticket.)
- The standalone `setlist_find` reply drops `; checking weekly for 2 months`, which only `concert_add` (the one that queues the show) may promise.
- `openclaw message send` gets 20 s per try instead of 60 and no pause after the last failed try; the documented `requestTimeoutMs` is 480000 (typical call 60–150 s, worst case about 8 min).
- A setlist-queue write that fails after the event was inserted is the note `Not queued for the setlist check: <Type>` on the reply instead of a crash without the calendar link.
- Teaser `not made (ari-spotify skill is not on this machine)` → `not made (Spotify is not set up: run \`concert-mcp spotify auth\`)`; the Spotify failure text is `str(ProviderError)[:160]` instead of the last stderr line (same content, no ANSI).
- Setlist: a missing setlist.fm key yields `lookup skipped (setlist.fm is not set up: …)` instead of silently omitting the bullet (the legacy only omitted it when the whole skill was absent).
- Teaser description: `every song Ari liked by them, in the order he liked them` → `every liked song by them, in the order they were liked`.
- `on Concerts and Personal` / `on Concerts or Personal` use the configured calendar names.
- FIX lines on the MCP path gain ` Call <tool> once more with only that change.`; the CLI prints the legacy line.
- New, additive: `clean_param`, bare-number title as pick, date-only start from the ticket text, two no-space time formats, `fix_guard`, `queue_status`, `healthcheck`, `calendar_add` as a named tool.
- The setlist.fm `User-Agent` is `concert-mcp/<version> (personal concert bot)`; the 1Password item default is `Spotify - concert-mcp`.
- The delete candidates list and the reply dedupe log moved from `~/.openclaw/media` to the state directory (`delete-candidates.json`, `last-sent.json`); both expire within minutes, so there is no fallback.

### Removed

- Shelling out to `spotify.zsh` for the teaser and the setlist lookup; both are in-process `urllib` clients now, and their failures are exceptions the flow turns into reply bullets.
- Module globals (`JSON_MODE`, `SEND`, the hard-coded `HOME`-relative paths); everything is a frozen `Settings` passed in.
- `sys.exit` and `die()` in library code; only `cli.py` exits, and only `cli.py` and `flows/auth.py` print.

[Unreleased]: https://github.com/AriSweedler/concert-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AriSweedler/concert-mcp/releases/tag/v0.1.0
