# concert-mcp design

One MCP server plus a CLI that carry Ari's concert stack: a ticket screenshot becomes a Google Calendar event, a Spotify
teaser playlist, a setlist.fm lookup, a queued weekly setlist check and a renamed Telegram topic. The model behind the
bot is a 9B local model, so the rule is absolute: **the model supplies a few plain strings; code owns every policy,
default, validation, idempotency guard, side effect and the exact reply text.**

The three legacy scripts (`cal-add.py`, `cal-del.py`, `spotify.py`) are the behavioural spec. Every reply string, FIX
text, record key, path default and guard they implement is carried over; the few deliberate wording changes are listed
at the end. The Google Calendar link is the `eventedit` form and nothing else: four forms were tested on a phone on
2026-10-25 and only `https://calendar.google.com/calendar/u/0/r/eventedit/<eid>` opened the event in the app.

## 1. Decisions

| Topic | Decision | Taken from | Rejected |
|---|---|---|---|
| Tool surface | 7 tools: `concert_add`, `calendar_add`, `event_delete` (with a `pick` param), `teaser_playlist`, `setlist_find`, `queue_status`, `healthcheck` | D1, D2 | D3's 8th tool `event_delete_pick` (one more tool for the small model to confuse) |
| Parameter types | every parameter is `str` with default `""`; required-ness is stated in the description and enforced in code, which returns the legacy `need()` reply or a FIX line | D1 risk #1 fallback, adopted up front | required params in the schema (a missing one yields a pydantic error blob the model loops on) |
| Tool return | plain `str` in the legacy `out()` contract: the NO_REPLY block, or reply + `NOT SENT: <why>`, or `FIX: <what>` | D1, D3 | D2's `ToolReply` dict (a 9B model paraphrases JSON; the legacy contract was written for exactly this reader). `sent: true/false` lives in the `--json` record and in the first line of the string |
| Weak-model concessions | `clean_param()` folds `null`/`None`/quotes; bare-number `title` in `event_delete` means `pick`; date-only `start` accepted when the ticket text states the show time; two no-space time formats; `fix_guard` turns the third FIX in 15 min into a reply asking the user | D1 | none |
| Error taxonomy | `FixInput` (caller's input wrong, never delivered), `NeedUser` (human must supply something, delivered), `ProviderError` (gws/Spotify/setlist.fm/openclaw failed, delivered with an operator hint) | D1/D2/D3 (same taxonomy, names chosen for OSS readers) | |
| Layering | `text/` pure, `adapters/` one class per side effect behind a Protocol, `flows/` orchestration returning `Outcome`, two shells `server.py` and `cli.py` | D3 | D1's flat layout (fewer disjoint build units) |
| Fakes | Protocol-level fakes in `tests/fakes.py`; adapters also take `runner=`/`urlopen=` kwargs so their own tests need no network | D1 (injectable runner/urlopen) + D3 (Protocol fakes) | shipping fakes in the package and a `--fake` CLI flag (surface without a user) |
| Config | frozen `Settings` from `CONCERT_MCP_*` env over `<config_dir>/config.toml` over defaults; XDG dirs; legacy `ari-spotify` paths read when the new path is absent; `concert-mcp migrate` copies them | D2/D3 (fallback) + D1 (migrate) | moving files by hand only |
| Personal data | operator name is `CONCERT_MCP_OPERATOR_NAME` (default `Admin`); every reply that said `Ari:` says `{operator}:`; "on the laptop" becomes "on the host"; calendar names, tz and 1Password item names are defaults a user overrides | D3 | hard-coding `Ari` (the brief forbids personal data; LICENSE carries the full name already, so this is hygiene, not secrecy) |
| Spotify / setlist.fm | in-process urllib clients; `die()` becomes exceptions; logging stays on stderr | all three | shelling to `spotify.zsh` |
| gws / openclaw / op | stay subprocesses, exactly the legacy argv | brief | |
| Delivery | `flows/deliver.py` is the only place that knows SENT / NOT SENT / NO_REPLY / FIX; dedupe (10 min) and the sent log live there; `Chat.send` only does the 3 tries | D3 | per-flow printing |
| Healthcheck | rows `PASS/FAIL/SKIP  %-16s evidence` like the homelab doctor, README anchor per FAIL, exit = FAIL count, `--probe` makes one real call per provider | D1/D2 | exit codes 2/3 for other CLI paths (legacy wrappers rely on 0/1) |
| OpenClaw timeout | `requestTimeoutMs: 480000` (typical cold call 60–150 s; worst case with every provider at its cap about 8 min) | D1, D3 | 240000 (covers the typical call, not a slow gws or a Telegram outage after the insert) |
| calendar_add `[Type]` prefix | not enforced (legacy does not); the tool description tells the model to keep it | D1, legacy | D2's CallerError on a missing prefix |
| Tests | goldens copied from the legacy strings, flows on fakes, adapters on fake runner/urlopen, one stdio smoke test, a README docs test, an optional env-gated legacy parity test | D1 (parity, goldens), D2 (docs test), D3 (fakes) | |

## 2. Repository layout

```
concert-mcp/
├── README.md                 what/why · architecture sketch · Getting started · Configuration · Credentials
│                             (### Google Calendar, ### Spotify, ### setlist.fm, ### OpenClaw) · OpenClaw config ·
│                             CLI usage · Development · Design principles · Migration from ari-spotify · License
├── LICENSE                   MIT, "Copyright (c) 2026 Ari Sweedler"
├── CHANGELOG.md              Keep a Changelog; [0.1.0] - 2026-10-25: extracted from the dotfiles skills + wording changes (§10)
├── CONTRIBUTING.md           uv sync · ruff · pytest · layering rule · no personal data · no print() outside cli/auth
├── .gitignore                .venv/ __pycache__/ .pytest_cache/ .ruff_cache/ dist/ *.egg-info .DS_Store *.local.toml
├── pyproject.toml            PEP 621 (see §9)
├── uv.lock                   pinned by `uv lock` (mcp 2.3.0); CI installs with `uv sync --locked --dev`
├── bin/healthcheck           #!/bin/sh  exec uv run --project "$(dirname "$0")/.." concert-mcp healthcheck "$@"
├── .github/workflows/ci.yml  lint · test matrix 3.11/3.12/3.13 · bare-healthcheck
├── .github/ISSUE_TEMPLATE/bug.md
├── docs/DESIGN.md            this file
├── docs/homelab-integration.md   patch-ready: check_concert_mcp/fix_concert_mcp, README paragraph, openclaw mcp set
├── docs/migration.md         legacy paths → new paths; what the dotfiles wrappers become
├── src/concert_mcp/
│   ├── __init__.py           __version__ = "0.1.0"
│   ├── __main__.py           python -m concert_mcp → cli.main()
│   ├── config.py             Settings, load_settings, env_table, redacted, legacy_paths, migrate
│   ├── log.py                stderr-only log_debug/info/warn/err, set_level, strip_ansi
│   ├── proc.py               run() → RunResult, never raises; Runner type
│   ├── result.py             Kind, Outcome, Delivery, FixInput, NeedUser, ProviderError, need(), NO_REPLY_TEXT
│   ├── text/                 pure: no I/O, no clock, no env
│   │   ├── __init__.py
│   │   ├── dates.py          parse_time, clock, show_time, apply_show_time, check_window, fmt_range, concert_date …
│   │   ├── titles.py         squash, normalize_name, split_openers, strip_concert_prefix, title_matches, clean_param …
│   │   ├── derive.py         regexes, derived_lines, openers_from_text, verify_line, merge_lines, coerce_description
│   │   ├── naming.py         playlist_name, canonical_concert_name, topic_name, teaser_description
│   │   ├── reply.py          bullet, bullets, event_link (eventedit), render_* for every reply, provider_reply
│   │   ├── candidates.py     Candidate, filter_candidates, search_window, duplicate_of, missing_link_lines …
│   │   ├── artists.py        Artist, TeaserResult, track_row, playlist_row, pick_artist, liked_hits, new_uris …
│   │   ├── setlists.py       Setlist, SetlistResult, setlist_row, rank_by_venue, near_date, search_url
│   │   ├── routes.py         ChatTarget, pick_live_route, chat_target, forum_topic, forum_target, user_texts
│   │   └── health.py         Check, HealthReport, render_health, ANCHORS
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── protocols.py      Calendar, SpotifyApi, SetlistFmApi, Chat, LiveContext, StateStore, Clock
│   │   ├── gws.py            GwsCalendar
│   │   ├── spotify_http.py   SpotifyCredentials, SpotifyClient
│   │   ├── setlistfm_http.py SetlistFmClient, load_api_key, save_api_key
│   │   ├── openclaw.py       OpenClawChat, OpenClawContext, NullContext
│   │   ├── state.py          JsonState
│   │   ├── clock.py          SystemClock
│   │   └── onepassword.py    op_available, op_read, op_store_field
│   ├── flows/
│   │   ├── __init__.py
│   │   ├── services.py       Services, build_services
│   │   ├── concert_add.py    AddRequest, PreparedEvent, prepare_event, resolve_calendar, add_event, add_concert, add_plain
│   │   ├── event_delete.py   DeleteRequest, find_candidates, remove, delete_event
│   │   ├── queue.py          QueueEntry, QueueStatus, make_entry, queue_add, queue_forget, queue_list, queue_status
│   │   ├── teaser.py         resolve_artist, liked_by_artist, make_teaser, teaser_outcome
│   │   ├── setlist.py        find_setlist, setlist_outcome
│   │   ├── playlists.py      spotify.py CRUD kept for the CLI
│   │   ├── auth.py           spotify_auth, setlistfm_auth (interactive, CLI only)
│   │   ├── deliver.py        deliver, render_tool_result, fix_guard, run_tool
│   │   └── healthcheck.py    run_healthcheck
│   ├── server.py             INSTRUCTIONS, build_server, the 7 tool functions, main
│   └── cli.py                build_parser, read_request, main
└── tests/
    ├── conftest.py           settings in tmp_path, frozen now, svc (fake services), ticket_text
    ├── fakes.py              FakeCalendar, FakeSpotify, FakeSetlistFm, FakeChat, FakeContext, MemoryState, FakeClock,
    │                         FakeRunner, FakeUrlopen
    ├── test_config.py  test_dates.py  test_titles.py  test_derive.py  test_naming.py  test_reply.py
    ├── test_candidates.py  test_artists.py  test_setlists.py  test_routes.py  test_health_render.py
    ├── test_gws.py  test_spotify_http.py  test_setlistfm_http.py  test_openclaw.py  test_state.py
    ├── test_flow_concert_add.py  test_flow_event_delete.py  test_flow_queue.py
    ├── test_flow_teaser.py  test_flow_setlist.py  test_flow_playlists.py
    ├── test_deliver.py  test_healthcheck.py
    ├── test_server.py  test_smoke_stdio.py  test_cli.py  test_readme.py  test_no_stdout_in_library.py
    └── test_legacy_parity.py (skipped unless CONCERT_MCP_LEGACY_DIR is set)
```

## 3. The output contract (what the model or the terminal sees)

Every tool and every CLI subcommand ends in `flows/deliver.py`:

1. The flow returns an `Outcome(kind, reply, error, fix, data)`. It never prints and never exits.
2. `deliver(outcome, svc, send)` decides delivery. No reply, `kind in (FIX, DRY_RUN)`, or `send=False` → not sent. No live
   route → `NOT SENT: no live chat (CLI run)`. Same text sent within 600 s → `NOT SENT: identical reply was sent less
   than 10 minutes ago`. Otherwise `Chat.send` (3 tries, 4 s apart) and the sent log is written.
3. `render_tool_result(outcome, delivery, tool=None)` renders exactly one of:
   - `FIX: <fix>` (+ ` Call <tool> once more with only that change.` when `tool` is given, i.e. on the MCP path)
   - the NO_REPLY block when `delivery.sent`:
     `NO_REPLY\nThe chat already received the full answer. Do not describe it or add anything. Your entire reply must be exactly: NO_REPLY`
   - `<reply>\n<delivery.status>` otherwise (the model relays the reply itself)
   - `json.dumps(outcome.to_json())` when there is no reply at all (healthcheck is never in this case; its reply is the table)
4. `--json` on the CLI prints `outcome.to_json() | {"sent": delivery.status}` instead: the legacy record.

Legacy record keys (`Outcome.to_json()`): `ok`, `duplicate`, `dryRun`, `retry`+`fix`, `error`, `reply`, then `data`
(`id`, `htmlLink`, `calendar`, `summary`, `start`, `end`, `location`, `description`, `dropped`, `teaser`, `setlist`,
`topic`, `topic_status`, `candidates`, `deleted`, …).

## 4. Tools (server.py)

Every parameter is `Annotated[Param, Field(description=...)]` with default `""`, where `Param = Annotated[str,
BeforeValidator(clean_param)]`: the schema stays `{"type": "string"}` and a number, a null or a quoted value is cleaned
before pydantic validates it, so the model never sees a schema error. Every tool body is:
build the request → flow → `fix_guard` → `deliver` → `render_tool_result(tool=name)` →
return the string. A `try/except Exception` around the whole body turns a crash into the `ProviderError`-shaped reply
`Something broke inside the concert tool (<Type>). <operator>: check the gateway log.` No tool ever raises or sets
`isError`.

| Tool | Parameters (all `str`, default `""`) | Description for the model (< 300 chars) |
|---|---|---|
| `concert_add` | `artist` "Headliner as printed on the ticket, e.g. \"Zed Fox\". Openers may stay in: \"Zed Fox with special guest Zork.\"" · `start` "Show date and time, e.g. \"2026-11-14 20:30\" or \"2026-11-14 8:30 PM\". Use the show time, not doors. A bare date is fine when the ticket text states the show time." · `venue` "Venue, and city when shown, e.g. \"The Quarry, San Francisco\"." · `source_text` "The ticket text you read these from (OCR or pasted email), whole and unedited. Leave empty if there is none." · `notes` "Extra facts, one per line, e.g. \"Seat: GA\". Leave empty when unsure; Doors, Purchased and Opener lines are found in the text automatically." | Add a concert to the calendar and do everything that goes with it: teaser playlist, setlist.fm lookup, the chat reply. Call once per ticket with artist, start and venue as read from the ticket; paste the ticket text into source_text. If the result starts with NO_REPLY, answer exactly NO_REPLY. |
| `calendar_add` | `title` "Event title exactly as the user wrote it, including a [Type] prefix if they used one, e.g. \"[Dinner] Team dinner\"." · `start` · `location` · `notes` · `calendar` "Calendar name when the user named one; empty means the personal calendar." | Add a plain (non-concert) event to the personal calendar, keeping the title as the user wrote it. For a concert ticket use concert_add instead. If the result starts with NO_REPLY, answer exactly NO_REPLY. |
| `event_delete` | `title` "Artist or event title to remove, e.g. \"Zed Fox\"." · `date` "Day of the event as YYYY-MM-DD when known." · `pick` "After the tool listed numbered matches and the user answered with a number, pass that number here, e.g. \"2\", and leave title empty." | Delete one calendar event by artist or title. When several match, the tool lists them with numbers and asks the user; when the user answers with a number, call this again with pick set to that number. If the result starts with NO_REPLY, answer exactly NO_REPLY. |
| `teaser_playlist` | `artist` · `date` "Concert date as YYYY-MM-DD." | Make or top up the Spotify teaser playlist for one concert: every song the user liked by the artist, oldest first, named "YYYY-MM-DD \| Artist". concert_add already does this; call only when asked for the playlist alone. NO_REPLY means answer exactly NO_REPLY. |
| `setlist_find` | `artist` · `date` · `venue` "Venue name to break ties." | Look up one show on setlist.fm by artist and date. concert_add already does this; call only when asked about a setlist alone. NO_REPLY means answer exactly NO_REPLY. |
| `queue_status` | none | List the concerts waiting for the weekly setlist.fm check. Takes no parameters. NO_REPLY means answer exactly NO_REPLY. |
| `healthcheck` | none | Report which credentials and tools are present for Google Calendar, Spotify, setlist.fm and OpenClaw, and what fixes each missing one. Takes no parameters. |

Annotations: `readOnlyHint` on `setlist_find`, `queue_status`, `healthcheck`; `destructiveHint` on `event_delete`.

`INSTRUCTIONS` (server instructions, < 2000 chars, injected into the system prompt by OpenClaw):

```
Concert tools (calendar, Spotify teaser, setlist.fm). Rules:
1. Call one tool once. Never call the same tool again unless its result starts with FIX:.
2. If a result starts with NO_REPLY, your entire reply is exactly: NO_REPLY
3. If a result starts with FIX:, change only what it names and call once more. If it fails again, tell the user in one sentence what you need from them.
4. Any other result: send its text to the user unchanged. Never invent a link, time or price.
5. Ticket screenshot or ticket text -> concert_add with artist, start (the show time, not doors; a bare date is fine when the text has the show time), venue, and the whole text in source_text.
6. Plain event (dinner, flight, appointment) -> calendar_add. Keep the user's [Type] prefix.
7. "delete"/"remove" an event -> event_delete. If the user answers a numbered list with a number, call event_delete with pick=that number.
8. Playlist alone -> teaser_playlist. Setlist alone -> setlist_find. "what is queued" -> queue_status. "are you healthy"/"what is missing" -> healthcheck.
9. Do not print tool calls as text; call the tool.
```

## 5. Configuration

Precedence: `CONCERT_MCP_<FIELD>` env var > `<config_dir>/config.toml` key (field name, lowercase) > default. The three
XDG dirs honour `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`. `concert-mcp config` prints the effective table,
redacted, with EXISTS / MISSING / LEGACY per path. No default carries personal data.

| Env var | Default | Meaning |
|---|---|---|
| `CONCERT_MCP_CONFIG_DIR` | `~/.config/concert-mcp` | credentials files and `config.toml` |
| `CONCERT_MCP_STATE_DIR` | `~/.local/state/concert-mcp` | `setlist-queue.json`, `delete-candidates.json`, `last-sent.json`, `fix-log.json` |
| `CONCERT_MCP_CACHE_DIR` | `~/.cache/concert-mcp` | `liked.json` |
| `CONCERT_MCP_SPOTIFY_CREDENTIALS` | `<config_dir>/spotify.json` | legacy fallback `~/.config/ari-spotify/credentials.json` |
| `CONCERT_MCP_SETLISTFM_CREDENTIALS` | `<config_dir>/setlistfm.json` | legacy fallback `~/.config/ari-spotify/setlistfm.json` |
| `CONCERT_MCP_LIKED_CACHE` | `<cache_dir>/liked.json` | legacy fallback `~/.cache/ari-spotify/liked.json` |
| `CONCERT_MCP_SETLIST_QUEUE` | `<state_dir>/setlist-queue.json` | legacy fallback `~/.local/state/ari-spotify/setlist-queue.json`; the weekly poller reads this file |
| `CONCERT_MCP_CANDIDATES_FILE` | `<state_dir>/delete-candidates.json` | no legacy fallback (lists are short-lived) |
| `CONCERT_MCP_SENT_LOG` | `<state_dir>/last-sent.json` | |
| `CONCERT_MCP_FIX_LOG` | `<state_dir>/fix-log.json` | the retry-loop breaker's memory |
| `CONCERT_MCP_GWS_BIN` | `which gws` else `gws` | |
| `CONCERT_MCP_GWS_KEY_FILE` | `~/.config/gws/.encryption_key` | exists → `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file` on every gws call |
| `CONCERT_MCP_GWS_TIMEOUT_S` | `60` | |
| `CONCERT_MCP_OPENCLAW_BIN` | `which openclaw` else `openclaw` | |
| `CONCERT_MCP_OPENCLAW_HOME` | `~/.openclaw` | base for the three below |
| `CONCERT_MCP_OPENCLAW_CONFIG` | `<openclaw_home>/openclaw.json` | read for `channels.telegram.botToken` only |
| `CONCERT_MCP_SESSION_DB` | `<openclaw_home>/agents/main/agent/openclaw-agent.sqlite` | opened read-only |
| `CONCERT_MCP_OCR_LAST` | `<openclaw_home>/media/ocr-last.txt` | |
| `CONCERT_MCP_TZ` | `America/Los_Angeles` | |
| `CONCERT_MCP_CONCERTS_CALENDAR` | `Concerts` | calendar name, resolved through `gws calendar calendarList list` |
| `CONCERT_MCP_DEFAULT_CALENDAR` | `primary` | id used for plain events and the second delete search |
| `CONCERT_MCP_DEFAULT_CALENDAR_NAME` | `Personal` | how replies name it |
| `CONCERT_MCP_CONTEXT_WINDOW_S` | `900` | live chat / OCR / transcript recency |
| `CONCERT_MCP_DEDUPE_WINDOW_S` | `600` | |
| `CONCERT_MCP_TEASER_TIMEOUT_S` | `150` | |
| `CONCERT_MCP_HTTP_TIMEOUT_S` | `30` | |
| `CONCERT_MCP_LIKED_CACHE_TTL_S` | `21600` | |
| `CONCERT_MCP_SETLIST_QUEUE_DAYS` | `61` | |
| `CONCERT_MCP_DEFAULT_DURATION_H` | `2` | |
| `CONCERT_MCP_PAST_GRACE_H` | `12` | |
| `CONCERT_MCP_MAX_FUTURE_DAYS` | `730` | |
| `CONCERT_MCP_SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8888/callback` | |
| `CONCERT_MCP_SPOTIFY_SCOPES` | `user-library-read playlist-read-private playlist-modify-private playlist-modify-public` | |
| `CONCERT_MCP_OP_VAULT` | `Private` | 1Password, auth commands only |
| `CONCERT_MCP_OP_SPOTIFY_ITEM` | `Spotify - concert-mcp` | fields `username` (client id), `credential` (secret), `refresh token` |
| `CONCERT_MCP_OP_SETLISTFM_ITEM` | `setlist.fm - API key` | field `credential` |
| `CONCERT_MCP_OPERATOR_NAME` | `Admin` | the name in "Admin: check `gws auth` on the host." |
| `CONCERT_MCP_SEND` | `1` | `0` disables reply delivery and topic renames everywhere |
| `CONCERT_MCP_RENAME_TOPICS` | `1` | |
| `CONCERT_MCP_DRY_RUN` | `0` | `1` makes every tool change nothing and return the reply it would have sent, `NOT SENT: dry run` |
| `CONCERT_MCP_LOG_LEVEL` | `info` | `debug` logs every subprocess argv (never tokens); stderr only |

OpenClaw snippet (README, docs/homelab-integration.md):

```
openclaw mcp set concert '{"command":"uv","args":["run","--project","/path/to/concert-mcp","concert-mcp","serve"],"env":{"CONCERT_MCP_OPERATOR_NAME":"${CONCERT_MCP_OPERATOR_NAME}","CONCERT_MCP_GWS_BIN":"${CONCERT_MCP_GWS_BIN}","CONCERT_MCP_OPENCLAW_BIN":"${CONCERT_MCP_OPENCLAW_BIN}"},"toolFilter":{"include":["concert_add","calendar_add","event_delete"]},"requestTimeoutMs":480000,"enabled":true}'
```

The default entry exposes the three chat jobs; the four operator tools (`teaser_playlist`, `setlist_find`, `queue_status`, `healthcheck`) are the CLI's, and the server registers all seven for an entry without `toolFilter`.

## 6. Module specifications

Foundations (§6.1–6.4) are written **in full by the scaffold**, because every other module imports them. Everything
else is stubbed by the scaffold with the exact signatures below and `raise NotImplementedError("TODO <unit>")` bodies,
then built by the unit named in §8.

### 6.1 `config.py` (scaffold)

```python
@dataclass(frozen=True)
class Settings:
    config_dir: Path; state_dir: Path; cache_dir: Path
    spotify_credentials: Path; setlistfm_credentials: Path; liked_cache: Path
    setlist_queue: Path; candidates_file: Path; sent_log: Path; fix_log: Path
    gws_bin: str; gws_key_file: Path; gws_timeout_s: int
    openclaw_bin: str; openclaw_home: Path; openclaw_config: Path; session_db: Path; ocr_last: Path
    tz: str; concerts_calendar: str; default_calendar: str; default_calendar_name: str
    context_window_s: int; dedupe_window_s: int; teaser_timeout_s: int; http_timeout_s: int
    liked_cache_ttl_s: int; setlist_queue_days: int; default_duration_h: int; past_grace_h: int; max_future_days: int
    spotify_redirect_uri: str; spotify_scopes: str
    op_vault: str; op_spotify_item: str; op_setlistfm_item: str
    operator_name: str; send: bool; rename_topics: bool; log_level: str
    legacy_in_use: frozenset[str]     # field names whose legacy path is the effective one

ENV_PREFIX = "CONCERT_MCP_"
def load_settings(env: Mapping[str, str] | None = None, home: Path | None = None) -> Settings
def read_config_file(path: Path) -> dict[str, str]          # tomllib; {} when absent; values coerced to str
def legacy_paths(home: Path, env: Mapping[str, str]) -> dict[str, Path]
    # spotify_credentials, setlistfm_credentials (~/.config/ari-spotify/{credentials,setlistfm}.json),
    # liked_cache ($XDG_CACHE_HOME/ari-spotify/liked.json), setlist_queue ($XDG_STATE_HOME/ari-spotify/setlist-queue.json)
def with_legacy_fallback(new: Path, legacy: Path) -> tuple[Path, bool]   # (new,False) if new exists; (legacy,True) if only legacy exists; else (new,False)
def env_table() -> list[tuple[str, str, str]]               # (ENV_NAME, default-as-doc, meaning) for every field; README and `config` print it
def redacted(settings: Settings) -> list[tuple[str, str, str]]  # (field, value, EXISTS|MISSING|LEGACY|-) ; never reads file contents
def migrate(settings: Settings, home: Path, *, dry: bool = False) -> list[tuple[Path, Path, str]]
    # copy each legacy file to its new path when the new one is absent; chmod 600 for the two credential files;
    # returns (src, dst, "copied" | "skipped: exists" | "skipped: no legacy"); never deletes
```

Booleans parse `1/true/yes/on` (case-insensitive). Ints raise `ValueError` with the env name. `load_settings` is pure
given `env` and `home`; tests pass both.

### 6.2 `log.py` (scaffold)

```python
def set_level(level: str) -> None        # debug | info | warn | error | off
def log_debug(msg: str) -> None
def log_info(msg: str) -> None           # "\033[32m[INFO]\033[0m msg" to stderr
def log_warn(msg: str) -> None           # yellow
def log_err(msg: str) -> None            # red
def kv(**fields: object) -> str          # "key='value' key2='value2'" (the skill_log record form)
def strip_ansi(s: str) -> str
```

Nothing in `src/` may call `print()` except `cli.py` and `flows/auth.py` (stdout is the MCP transport); a test greps.

### 6.3 `proc.py` (scaffold)

```python
@dataclass(frozen=True)
class RunResult:
    returncode: int; stdout: str; stderr: str; error: str | None   # error on FileNotFoundError/TimeoutExpired/OSError; returncode then -1
    @property
    def ok(self) -> bool
    @property
    def tail(self) -> str                 # (stderr or stdout).strip()[-300:]
def run(cmd: Sequence[str], timeout_s: float, *, env: Mapping[str, str] | None = None, cwd: str | Path | None = None,
        redact_from: int | None = None) -> RunResult
    # capture_output, text, utf-8; never raises; log_debug(argv) at debug level, argv[redact_from:] shown as "<redacted>"
Runner = Callable[..., RunResult]
```

### 6.4 `result.py` (scaffold)

```python
class Kind(StrEnum): OK="ok"; DUPLICATE="duplicate"; DRY_RUN="dry_run"; NEED_USER="need_user"; FIX="fix"; ERROR="error"

@dataclass(frozen=True)
class Outcome:
    kind: Kind
    reply: str | None = None
    error: str | None = None
    fix: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    @property
    def ok(self) -> bool                  # OK, DUPLICATE, DRY_RUN
    @property
    def exit_code(self) -> int            # 0 when ok else 1
    def to_json(self) -> dict[str, Any]   # {"ok":..., ["duplicate":True], ["dryRun":True], ["retry":True,"fix":...], ["error":...], ["reply":...], **data}

@dataclass(frozen=True)
class Delivery:
    sent: bool
    status: str                           # "SENT to telegram <chat> topic <n>" | "NOT SENT: <why>"

class FixInput(Exception):   def __init__(self, error: str, fix: str)
class NeedUser(Exception):   def __init__(self, error: str, reply: str)
class ProviderError(Exception): def __init__(self, provider: str, error: str, reply: str)

NEED_USER = "I could not read the {what} from that. Reply with it and I will add the event."
NO_REPLY_TEXT = "NO_REPLY\nThe chat already received the full answer. Do not describe it or add anything. Your entire reply must be exactly: NO_REPLY"
def need(what: str) -> NeedUser                              # NeedUser(f"{what} is required", NEED_USER.format(what=what))
def outcome_from(exc: FixInput | NeedUser | ProviderError) -> Outcome
    # FixInput → Outcome(FIX, error, fix); NeedUser → Outcome(NEED_USER, reply, error); ProviderError → Outcome(ERROR, reply, error, data={"provider":...})
def crash_outcome(exc: BaseException, operator: str) -> Outcome
    # Outcome(ERROR, reply=f"Something broke inside the concert tool ({type(exc).__name__}). {operator}: check the gateway log.", error=repr(exc)[:300])
```

### 6.5 `text/dates.py` — unit **text-parse**

```python
START_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %I:%M %p", "%Y-%m-%d %I %p",
                 "%Y-%m-%d %I:%M%p", "%Y-%m-%d %I%p", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S")   # legacy six + two no-space
def parse_time(s: str, tz: str, what: str = "start") -> datetime
    # squash whitespace; try START_FORMATS (upper-cased when the format has %p); then fromisoformat(s.replace(" ","T",1).replace("Z","+00:00"));
    # naive → ZoneInfo(tz). FixInput(f"{what} {s!r} is not a date", f'{what} must look like "2026-11-03 20:00" (24h) or "2026-11-03 8:00 PM"')
def is_date_only(s: str) -> bool                              # ^\d{4}-\d{2}-\d{2}$ after strip
def validate_tz(tz: str) -> str                               # FixInput(f"unknown tz {tz!r}", "use an IANA name like America/Los_Angeles, or omit it")
def clock(txt: str) -> time                                   # "7:30 PM" / "7pm" / "7.30 p.m." (legacy clock())
def doors_time(text: str) -> time | None                      # doors?\s*(?:open|at|:|@)?\s*(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)
def show_time(text: str) -> tuple[time | None, bool]
    # certain: \b(?:show(?:time)?|music|starts?|performance)\s*(?:at|:|@|starts?|begins?)?\s*(<clock>) ; inferred: doors + exactly one other CLOCK_RE time
def default_end(start: datetime, hours: int = 2) -> datetime
def check_duration(start: datetime, end: datetime) -> None    # FixInput("end must be after start and within 12 hours", "fix end, or omit it for start + 2 hours") unless start < end <= start+12h
def past_reply(start: datetime) -> str                        # f"That reads as {start:%a %b %-d %Y, %-I:%M %p}, which is in the past. Reply with the right date (include the year) and I will add it."
def far_future_reply(start: datetime) -> str                  # f"That reads as {start:%a %b %-d %Y}, over two years from now. Reply with the right date and I will add it."
def check_window(start: datetime, now: datetime, *, allow_past: bool, past_grace_h: int = 12, max_future_days: int = 730) -> None
    # NeedUser(f"start {start.isoformat()} is in the past", past_reply(start)) / NeedUser(f"start {start.isoformat()} is over two years out", far_future_reply(start))
def apply_show_time(start: datetime, end: datetime, end_assumed: bool, text: str) -> tuple[datetime, datetime, list[str]]
    # st, certain = show_time(text); if st and start.time() != st: fixed = start.replace(hour, minute);
    #   certain → (fixed, end + (fixed-start) if end_assumed else end, [f"Start corrected to {fixed:%-I:%M %p}, the show time in the text."])
    #   inferred → FixInput(f"start {start:%-I:%M %p} is not the show time", f'set start to "{fixed:%Y-%m-%d %-I:%M %p}" (the show time in the text; {start:%-I:%M %p} is doors)')
def start_from_date(date_str: str, text: str, tz: str) -> tuple[datetime, str]
    # NEW: date-only start; st, _ = show_time(text); st None → NeedUser("show time is required", NEED_USER.format(what="show time"));
    # returns (date+st in tz, f"Start taken from the text ({st:%-I:%M %p}).")
def fmt_range(start: datetime, end: datetime) -> str          # "Sat Nov 14, 8:30 PM – 10:30 PM PST" (cross-day repeats the date; en dash)
def concert_date(text: str) -> str                            # YYYY-MM-DD | YYYY/MM/DD | RFC3339 prefix → "YYYY-MM-DD"; FixInput(f"unreadable concert date {text!r}", 'date must look like "2026-11-03"')
def when_of(event: Mapping[str, Any], tz: ZoneInfo) -> tuple[str, datetime]   # fmt_range for dateTime; f"{day:%a %b %-d} (all day)" for date (cal-del when_of)
```

### 6.6 `text/titles.py` — unit **text-parse**

```python
OPENER_SPLIT_RE = re.compile(r"^(.+?)\s+(?:with|w/|\+|featuring|feat\.?)\s+(?:special guests?\s+|support from\s+)?(.+)$", re.I)
@dataclass(frozen=True)
class SplitTitle: title: str; openers: tuple[str, ...]
def squash(s: object) -> str                                  # re.sub(r"\s+"," ",str(s or "")).strip()
def clean_param(v: object) -> str                             # NEW: squash; strip one pair of matching quotes; None/"null"/"none"/"n/a"/"undefined" (ci) → ""
def looks_like_pick(s: str) -> bool                           # ^\s*\d{1,2}\.?\s*$
def normalize_name(s: object) -> str                          # lower, "&"→"and", whitespace collapsed (spotify.normalize == cal-del.norm_title)
def split_opener_list(s: str) -> list[str]                    # re.split(r",|\s+and\s+|\s*&\s*|\s*/\s*"), stripped, non-empty
def split_openers(title: str) -> SplitTitle                   # headliner must be >= 2 chars, else (title, ())
def strip_concert_prefix(title: str) -> str                   # re.sub(r"^\[concert\]\s*", "", title, flags=re.I)
def title_matches(wanted: str, have: str) -> bool             # normalized: wanted in have, or every word of wanted with len>1 in have; empty wanted → True
```

### 6.7 `text/derive.py` — unit **text-parse**

```python
PLATFORMS = ("Ticketweb", "Ticketmaster", "AXS", "Eventbrite", "DICE", "Dothebay", "See Tickets", "Etix", "Tixr", "Shotgun", "RA")
URL_RE = re.compile(r"https?://\S+", re.I); MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
CLOCK_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.I); PLAIN_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
def derived_lines(text: str) -> list[str]                     # ["Doors: 7:30 PM", "Purchased: 2 tickets, $42 on Ticketweb"] exactly as legacy derived_lines()
def openers_from_text(text: str) -> list[str]                 # ^\s*(?:with|w/)\s+(?:special guests?\s+|support from\s+)(.+?)\s*$  (re.I|re.M) → split_opener_list
def norm_claims(s: object) -> str                             # re.sub(r"\s+","",str(s)).lower().rstrip("/.,)")
def build_source(caller_source: str | None, context_text: str) -> tuple[str, str | None]   # ("\n".join(non-empty), norm_claims(raw) or None when nothing)
def verify_line(line: str, source_norm: str | None) -> bool   # claims = URL+MONEY+CLOCK+PLAIN; none → True; source None → False; all(norm_claims(c) in source_norm)
def merge_lines(*, derived: Sequence[str], openers: Sequence[str], caller: Sequence[str], source_norm: str | None, title: str) -> tuple[list[str], list[str]]
    # kept = derived + [f"Opener: {o}" for o in openers if o.lower() != title.lower()];
    # for each caller line (squashed, non-empty): skip when its key (before ":") equals an existing key (ci); verify_line → kept else dropped
def coerce_description(desc: object) -> list[str]             # None → []; str → replace("\\n","\n").split("\n"); list → [str(x)]; else FixInput("description must be a list of lines", 'use ["Doors: 7:00 PM", "Purchased: 1 ticket, $45"]')
def check_attendees(att: object) -> list[str]                 # None → []; list with "@" in each → list; else FixInput("attendees must be a list of email addresses", 'use ["name@example.com"]')
```

### 6.8 `text/naming.py` — unit **text-render**

```python
CONCERT_NAME = "{date} | {artist}"
OLD_CONCERT_NAME = re.compile(r"^\s*(\d{4})[/-](\d{2})[/-](\d{2})\s*(?:[-|]\s*)?(.+?)\s*$")
def playlist_name(date: str, artist: str) -> str
def canonical_concert_name(name: str | None) -> str | None
def topic_name(start: datetime, title: str, location: str) -> str       # f"{start:%Y-%m-%d} | {title} | {location.split(',')[0].strip()}"
def teaser_description(artist: str, date: str) -> str                   # f"Teaser for {artist} on {date}: every liked song by them, in the order they were liked."
```

### 6.9 `text/reply.py` — unit **text-render**

```python
from concert_mcp.result import NO_REPLY_TEXT   # re-exported
def bullet(key: str, value: str) -> str                                 # f"• **{key}:** {value}"
def bullets(title: str, when: str, location: str, lines: Sequence[str]) -> str   # Artist/When/Where, then "K: v" with len(K)<=24 → bullet(K,v) else f"• {line}"
def event_link(html_link: str | None) -> str | None
    # eid = parse_qs(urlparse(html_link).query)["eid"][0] → f"https://calendar.google.com/calendar/u/0/r/eventedit/{eid}";
    # html_link unchanged when it has no eid; None → None. THE ONLY LINK FORM (phone test 2026-10-25). No alternatives, no fallback forms.
def render_added(*, calendar: str, body: str, notes: Sequence[str], html_link: str | None, duplicate: bool) -> str
    # head = f"**Already on {calendar}** (nothing new created)" if duplicate else f"**Added to {calendar}**"
    # f"{head}\n{body}" + "".join("\n"+n for n in notes) + f"\n[Open in Google Calendar]({event_link(html_link)})"
def render_dry_add(calendar: str, body: str, notes: Sequence[str]) -> str   # f"**Would add to {calendar}**\n{body}" + notes
def dropped_note(dropped: Sequence[str]) -> str                         # f"Left out (not in the text I was given): {'; '.join(dropped)}"
def teaser_bullet(res: TeaserResult | None, *, timeout_s: int, dry: bool) -> str | None   # None when res is None (teaser not wanted). Texts:
    # unavailable → bullet("Teaser", "not made (Spotify is not set up: run `concert-mcp spotify auth`)")
    # timeout    → bullet("Teaser", f"not made (Spotify took longer than {timeout_s} s)")
    # failed     → bullet("Teaser", f"not made ({res.reason[:160]})")
    # none       → bullet("Teaser", f"none, no liked songs by {res.artist.name} yet")
    # dry_run or no url → bullet("Teaser", f"would make {res.name} ({res.liked_count} liked songs)")
    # created/updated → bullet("Teaser", f"[{res.name}]({res.url}) {res.liked_count} liked songs{'' if created else ' (already existed, topped up)'}. Move it to Concerts › Teasers in Spotify.")
def teaser_line(res: TeaserResult | None) -> str | None                 # f"Teaser playlist: {res.url}" for created/updated with a url
def setlist_bullet(res: SetlistResult | None) -> str | None             # None when res is None. Texts:
    # unavailable → bullet("Setlist", "lookup skipped (setlist.fm is not set up: run `concert-mcp spotify setlist-auth`)")
    # failed      → bullet("Setlist", f"lookup failed ({res.reason[:160]})")
    # not found   → bullet("Setlist", "not on setlist.fm yet" + weekly)
    # found       → bullet("Setlist", f"[setlist.fm]({res.url}) {f'{n} songs so far' if n else 'no songs yet'}" + weekly)
    # weekly = "; checking weekly for 2 months" when queued=True (concert_add, which queues the show); "" for the standalone tool
def setlist_line(res: SetlistResult | None) -> str | None               # f"Setlist: {res.url}" when found
LINK_LINE_PREFIXES = ("Teaser playlist: ", "Setlist: ")
def render_body(title: str, when: str, location: str, lines: Sequence[str], extras: Sequence[str | None]) -> str
    # bullets(title, when, location, [l for l in lines if not l.startswith(LINK_LINE_PREFIXES)]) + "\n"+each non-None extra
def render_removed(c: Candidate, dry: bool) -> str                      # legacy removed_reply: head, Artist, When, Where (if location), Teaser left-in-place bullet per "Teaser playlist: " description line
def render_candidates(title: str, cands: Sequence[Candidate]) -> str    # f"I found {n} events like **{title}**. Reply with the number to delete:\n" + rows f"{i}. **{summary}** on {calendar}: {when}" + (f", {location}")
def render_none_found(title: str, date: str | None, calendar_names: Sequence[str]) -> str   # f"I found no event like **{title}**{f' around {date}' if date else ''} on {' or '.join(names)}. Nothing deleted."
def render_would_look(title: str, date: str | None, calendar_names: Sequence[str]) -> str   # f"**Would look for** {title}{around} on {' and '.join(names)}, then remove the one match."
NO_CANDIDATES_REPLY = "I do not have a list to pick from any more. Tell me the event name and date and I will look again."
WHICH_EVENT_REPLY = "Which event? Tell me the artist or title (and the date if you know it) and I will remove it."
def render_teaser_standalone(res: TeaserResult, *, timeout_s: int, dry: bool) -> str   # "**Teaser playlist**\n" + teaser_bullet(...)
def render_setlist_standalone(res: SetlistResult) -> str                # "**Setlist**\n" + bullet; not found adds f" Search: {res.search_url}"
def render_queue(status: QueueStatus) -> str                            # "The setlist queue is empty." | f"**Setlist queue** ({n} shows)\n" + "• {date} {artist} @ {venue} — {n} songs, checking until {until}" (expired rows end with " (expired)")
def provider_reply(provider: str, operator: str, *, what: str = "request") -> str
    # "gws"            → f"Google Calendar refused the {what}. {operator}: check `gws auth` on the host."      (what = "request" | "delete")
    # "gws-nojson"     → f"Google Calendar gave an unreadable answer. {operator}: check the host."
    # "gws-keychain"   → f"The login keychain is locked in this session, so I did not touch Google Calendar. {operator}: create the gws file keyring key (README.md#google-calendar) or run this from a session with keychain access."
    # "gws-missing"    → "The calendar tool is not reachable right now. Try again in a minute."
    # "spotify-unconfigured"   → f"Spotify is not set up on this machine. {operator}: run `concert-mcp spotify auth`."
    # "setlistfm-unconfigured" → f"setlist.fm is not set up on this machine. {operator}: run `concert-mcp spotify setlist-auth`."
    # "spotify" / "setlistfm"  → f"{Spotify|setlist.fm} refused the request. {operator}: check the host log."
```

### 6.10 `text/candidates.py` — unit **text-render**

```python
@dataclass(frozen=True)
class Candidate:
    id: str; calendar_id: str; calendar: str; summary: str; when: str; start: str; location: str; html_link: str | None; description: str
    def to_json(self) -> dict[str, Any]            # legacy keys: id, calendarId, calendar, summary, when, start, location, htmlLink, description
    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "Candidate"
def candidate_from_event(event: Mapping[str, Any], calendar_id: str, calendar_name: str, tz: ZoneInfo) -> Candidate   # summary or "(no title)"
def filter_candidates(events: Iterable[Mapping[str, Any]], wanted_title: str, calendar_id: str, calendar_name: str, tz: ZoneInfo) -> list[Candidate]   # drops status=="cancelled"; title_matches
def search_window(date: str | None, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]
    # date → [day-1d, day+2d) with day = strptime(date[:10], "%Y-%m-%d") in tz; None → [now-30d, now+365d);
    # bad date → FixInput(f"date {date!r} is not YYYY-MM-DD", 'send the day as "2026-10-24"')
def duplicate_of(events: Iterable[Mapping[str, Any]], title: str) -> Mapping[str, Any] | None   # (summary or "").strip().lower() == title.lower() and status != "cancelled"
def missing_link_lines(description: str | None, lines: Sequence[str | None]) -> list[str]        # lines not None and not in (description or "")
def patched_description(description: str | None, missing: Sequence[str]) -> str                  # "\n".join([(description or "").rstrip(), *missing]).strip()
def teaser_url_in(description: str) -> str | None                                                 # value of the "Teaser playlist: " line
def pick_index(n: str) -> int | None                                                               # int(n.strip().rstrip(".")) or None
```

### 6.11 `text/artists.py` — unit **text-music**

```python
@dataclass(frozen=True)
class Artist: name: str; id: str; url: str | None
class NoArtistMatch(Exception):
    def __init__(self, query: str, top_names: Sequence[str]) -> None   # str(): legacy "no Spotify artist matches | artist='X'" when no results, else "no close Spotify artist match | artist='X' top_results='A, B'"
@dataclass(frozen=True)
class TeaserResult:
    status: Literal["created", "updated", "none", "dry_run", "unavailable", "timeout", "failed"]
    artist: Artist | None; date: str; name: str | None; liked_count: int
    url: str | None = None; id: str | None = None; added: int = 0; already_there: int = 0
    tracks: tuple[str, ...] = (); exists: bool = False; reason: str | None = None
    def to_json(self) -> dict[str, Any]
        # legacy cmd_teaser record: artist{name,id,url}, date, name, liked_count, tracks, created, [dry_run, exists], [id, url, added, already_there,
        # folder="Concerts / Teasers (move it there in the Spotify app; the API cannot)", reply=f"Teaser playlist {created|updated}: {name} ({n} liked songs) {url}"]
        # none → reply=f"No teaser: you have not liked any songs by {artist} yet."; failed/unavailable/timeout → {"ok": False, "error": reason}
def track_row(track: Mapping[str, Any], added_at: str | None = None) -> dict[str, Any]
def playlist_row(pl: Mapping[str, Any]) -> dict[str, Any]           # tracks total from `items` or `tracks`
def item_track(entry: Mapping[str, Any]) -> Mapping[str, Any] | None  # entry["item"] or entry["track"] when type == "track"
def pick_artist(query: str, items: Sequence[Mapping[str, Any]]) -> tuple[Artist, bool]   # exact normalized (most followers) → word-subset either way (most followers, corrected=True) → NoArtistMatch
def liked_hits(rows: Sequence[Mapping[str, Any]], artist: Artist) -> list[dict[str, Any]]  # artist.id in artist_ids or normalized name in artists; order kept
def sort_oldest_first(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]           # key added_at or ""
def new_uris(wanted: Sequence[str], have: Iterable[str]) -> list[str]
def validate_track_uris(uris: Sequence[str]) -> list[str]                                  # FixInput(f"not track uris: {bad}", "use spotify:track:<id>")
def find_playlist_named(playlists: Iterable[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None   # normalize_name equality
```

### 6.12 `text/setlists.py` — unit **text-music**

```python
@dataclass(frozen=True)
class Setlist:
    id: str; url: str; artist: str; date: str; venue: str; city: str | None; tour: str | None; song_count: int; songs: tuple[str, ...]
    def to_json(self, songs: bool = False) -> dict[str, Any]
@dataclass(frozen=True)
class SetlistResult:
    found: bool; match: Literal["exact date", "within a day", "none", "unavailable", "failed"]
    artist: str; date: str; venue: str | None
    setlist: Setlist | None = None; search_url: str | None = None; reason: str | None = None
    @property
    def url(self) -> str | None
    @property
    def song_count(self) -> int
    def to_json(self, songs: bool = False) -> dict[str, Any]   # legacy setlist-find record: found, match, id,url,artist,date,venue,city,tour,song_count[,songs] | found False, artist, date, venue, match, search_url
def setlist_row(s: Mapping[str, Any]) -> Setlist               # eventDate dd-MM-yyyy → yyyy-MM-dd; songs from sets.set[].song[].name
def setlistfm_date(date: str) -> str                           # "YYYY-MM-DD" → "dd-MM-yyyy"
def rank_by_venue(rows: Sequence[Setlist], venue: str | None) -> list[Setlist]            # stable sort by -len(shared normalized words) when venue and len>1
def near_date(rows: Iterable[Setlist], date: str, venue: str | None, max_days: int = 1) -> list[Setlist]   # |delta|<=1 and (no venue or shared word); nearest first; unparsable dates skipped
def search_url(artist: str) -> str                             # f"https://www.setlist.fm/search?query={quote(artist)}"
```

### 6.13 `text/routes.py` — unit **text-music**

```python
@dataclass(frozen=True)
class ChatTarget: channel: str; chat: str; thread: str | None; account: str | None
def pick_live_route(rows: Iterable[tuple[str, str]], cutoff_ms: float) -> tuple[str | None, dict[str, Any] | None]
    # rows = (current_session_id, entry_json); newest lastInteractionAt > cutoff_ms with delivery.route.channel; garbage JSON skipped
def chat_target(route: Mapping[str, Any]) -> ChatTarget | None        # to = route.target.to; chat = re.sub("^telegram:","",to).split(":topic:")[0]; thread = route.thread.id; account = route.accountId; None when chat empty
def forum_topic(route: Mapping[str, Any] | None) -> tuple[str, str] | None   # (chat, thread) iff channel=="telegram", thread set and != "1", chat.startswith("-100"); judges the raw target.to
def forum_target(target: ChatTarget | None) -> tuple[str, str] | None        # the same rule on chat_target(route): the bare chat id, whatever prefix/suffix the store's `to` carries; the flows use this one
def user_texts(event_rows: Iterable[tuple[str]]) -> list[str]         # message.role=="user"; content str, or list of {"type":"text","text"}
```

### 6.14 `text/health.py` — unit **flows-deliver-health**

```python
Status = Literal["PASS", "FAIL", "SKIP"]
@dataclass(frozen=True)
class Check: name: str; status: Status; evidence: str; anchor: str | None = None; fix: str | None = None
@dataclass(frozen=True)
class HealthReport:
    checks: tuple[Check, ...]
    @property
    def failures(self) -> int
    @property
    def ok(self) -> bool
    def to_json(self) -> dict[str, Any]
ANCHORS = {"getting-started": "README.md#getting-started", "configuration": "README.md#configuration", "google-calendar": "README.md#google-calendar",
           "spotify": "README.md#spotify", "setlistfm": "README.md#setlistfm", "openclaw": "README.md#openclaw"}
def render_health(report: HealthReport) -> str
    # one line per check: f"{status}  {name:<16} {evidence}"; then per FAIL f"  ->  {name:<16} {fix} ({ANCHORS[anchor]})"; last line f"{n} failing" or "all good"
```

### 6.15 `adapters/protocols.py` (scaffold)

```python
class Calendar(Protocol):
    def list_calendars(self) -> list[dict[str, Any]]: ...
    def list_events(
        self, calendar_id: str, *, time_min: datetime, time_max: datetime, q: str = "", max_results: int = 50
    ) -> list[dict[str, Any]]: ...
    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]: ...
    def insert_event(
        self,
        calendar_id: str,
        *,
        summary: str,
        start: datetime,
        end: datetime,
        location: str,
        description: str | None,
        attendees: Sequence[str],
    ) -> dict[str, Any]: ...
    def patch_description(self, calendar_id: str, event_id: str, description: str) -> None: ...
    def delete_event(self, calendar_id: str, event_id: str) -> None: ...


class SpotifyApi(Protocol):
    def me(self) -> dict[str, Any]: ...
    def search_artists(self, q: str, limit: int = 10) -> list[dict[str, Any]]: ...
    def liked_rows(
        self, refresh: bool = False, deadline: float | None = None
    ) -> list[dict[str, Any]]: ...  # track_row dicts oldest first (cached)
    def my_playlists(self, deadline: float | None = None) -> list[dict[str, Any]]: ...
    def playlist(self, playlist_id: str, fields: str) -> dict[str, Any]: ...
    def playlist_items(self, playlist_id: str, deadline: float | None = None) -> list[dict[str, Any]]: ...
    def create_playlist(self, *, name: str, description: str, public: bool) -> dict[str, Any]: ...  # POST /me/playlists
    def add_items(self, playlist_id: str, uris: Sequence[str]) -> None: ...  # chunks of 100
    def rename_playlist(self, playlist_id: str, name: str) -> None: ...
    def unfollow_playlist(self, playlist_id: str) -> None: ...
    @property
    def user_id(self) -> str | None: ...


class SetlistFmApi(Protocol):
    def search_setlists(
        self, artist: str, *, date: str | None = None, year: str | None = None, page: int = 1
    ) -> dict[str, Any] | None: ...  # None on 404


class Chat(Protocol):
    def send(
        self, target: ChatTarget, text: str
    ) -> str: ...  # "SENT to <channel> <chat>[ topic n]" | "NOT SENT: ..."; never raises
    def rename_topic(
        self, chat_id: str, thread_id: str, name: str
    ) -> str: ...  # "topic renamed to X" | "topic not renamed: ..."; never raises


class LiveContext(Protocol):
    def live_route(self, now_s: float) -> tuple[str | None, dict[str, Any] | None]: ...
    def recent_text(self, now_s: float, session_id: str | None) -> str: ...


class StateStore(Protocol):
    def read_json(self, path: Path, default: T) -> T: ...
    def write_json(
        self, path: Path, obj: Any, *, mode: int | None = None
    ) -> None: ...  # mkdir -p, tmp file, os.replace
    def remove(self, path: Path) -> None: ...
    def mtime(self, path: Path) -> float | None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...  # aware UTC
    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
```

### 6.16 `adapters/gws.py` — unit **adapter-gws**

```python
class GwsCalendar:   # Calendar
    def __init__(self, settings: Settings, runner: Runner = run, *, platform: str = sys.platform, environ: Mapping[str, str] | None = None) -> None
    def env(self) -> dict[str, str]                 # environ + GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file when settings.gws_key_file exists
    def keychain_reachable(self) -> bool            # darwin: runner(["security","show-keychain-info", ~/Library/Keychains/login.keychain-db]).ok; else True
    def raw(self, *args: str, cwd: str | None = None) -> str
        # not file backend and not keychain_reachable → ProviderError("gws-keychain", "login keychain not reachable from this session; gws would delete its credentials", provider_reply("gws-keychain", op))
        # r = runner([gws_bin, *args], gws_timeout_s, env=env(), cwd=cwd); r.error → ProviderError("gws-missing", f"gws did not run: {r.error}", provider_reply("gws-missing", op))
        # rc != 0 → ProviderError("gws", f"gws {' '.join(args[:2])} exited {rc}: {r.tail}", provider_reply("gws", op, what="delete" if args[1:3]==["events","delete"] else "request"))
        # returns r.stdout (stderr is keyring chatter, never parsed)
    def json(self, *args: str) -> Any               # json.loads(raw) or ProviderError("gws-nojson", "gws returned no JSON", provider_reply("gws-nojson", op))
    def list_calendars(self)                        # json("calendar","calendarList","list").get("items", [])
    def list_events(...)                            # json("calendar","events","list","--params", json.dumps({"calendarId","timeMin","timeMax","singleEvents":True,"maxResults","q"})).get("items", [])
    def get_event(...)                              # json("calendar","events","get","--params", json.dumps({"calendarId","eventId"}))
    def insert_event(...)                           # json("calendar","+insert","--calendar",id,"--summary",s,"--start",start.isoformat(),"--end",end.isoformat(),"--location",loc, ["--description",d], ["--attendee",a]*)
    def patch_description(...)                      # raw("calendar","events","patch","--params", json.dumps({"calendarId","eventId"}), "--json", json.dumps({"description": d}))
    def delete_event(...)                           # raw("calendar","events","delete","-o","gws-del.out","--params", json.dumps({...}), cwd=tempfile.mkdtemp()); the out file and dir removed after
```

### 6.17 `adapters/spotify_http.py` — unit **adapter-music**

```python
ACCOUNTS = "https://accounts.spotify.com"; API = "https://api.spotify.com/v1"; PAGE = 50
@dataclass
class SpotifyCredentials:
    client_id: str; client_secret: str; refresh_token: str; access_token: str | None = None; expires_at: float = 0.0; scope: str | None = None; user_id: str | None = None
    @classmethod
    def load(cls, path: Path) -> "SpotifyCredentials"    # ProviderError("spotify-unconfigured", f"not authorized yet | path='{path}' hint='run: concert-mcp spotify auth'", provider_reply(...))
    def save(self, path: Path, state: StateStore) -> None   # write_json(mode=0o600)
class SpotifyClient:   # SpotifyApi
    def __init__(self, settings: Settings, clock: Clock, state: StateStore, *, urlopen: Callable = urllib.request.urlopen, creds: SpotifyCredentials | None = None) -> None
    @classmethod
    def if_configured(cls, settings, clock, state, **kw) -> "SpotifyClient | None"   # None when settings.spotify_credentials is absent
    def access_token(self) -> str                      # refresh when expires_at <= now+60; persists rotated refresh_token; token errors → ProviderError("spotify", "token request refused | grant=... status=... body=...", ...)
    def api(self, method: str, path: str, *, params: Mapping[str, Any] | None = None, body: Any = None, retries: int = 3) -> Any
        # 429 → sleep(min(Retry-After, 30)) and retry; 401 → expires_at=0 and retry; other HTTPError → ProviderError("spotify", f"Spotify API refused | method='{m}' path='{p}' status='{code}' body='{body[:300]}'", ...); URLError → "Spotify API unreachable | ..."
    def paged(self, path: str, params: Mapping[str, Any] | None = None, deadline: float | None = None) -> Iterator[dict[str, Any]]
        # limit=PAGE; follows next; between pages clock.time() > deadline → ProviderError("spotify-timeout", f"Spotify took longer than {teaser_timeout_s} s", ...)
    def me(self); search_artists(q, limit=10); my_playlists(); playlist(id, fields); playlist_items(id); create_playlist(*, name, description, public); add_items(id, uris); rename_playlist(id, name); unfollow_playlist(id); user_id
    def liked_rows(self, refresh: bool = False, deadline: float | None = None) -> list[dict[str, Any]]
        # cache at settings.liked_cache for liked_cache_ttl_s (state.mtime); a cache whose first row lacks artist_ids is refetched; rows sorted oldest first; log_info counts
```

### 6.18 `adapters/setlistfm_http.py` — unit **adapter-music**

```python
SETLISTFM_API = "https://api.setlist.fm/rest/1.0"
def user_agent() -> str                               # f"concert-mcp/{__version__} (personal concert bot)"
def load_api_key(path: Path) -> str | None            # json {"api_key"}; None when absent/unreadable
def save_api_key(path: Path, key: str, state: StateStore) -> None   # write_json({"api_key": key}, mode=0o600)
class SetlistFmClient:   # SetlistFmApi
    def __init__(self, settings: Settings, clock: Clock, *, urlopen: Callable = urllib.request.urlopen, api_key: str | None = None) -> None   # key from file when None; missing → ProviderError("setlistfm-unconfigured", f"no setlist.fm key yet | path='{p}' hint='run: concert-mcp spotify setlist-auth'", ...)
    @classmethod
    def if_configured(cls, settings, clock, **kw) -> "SetlistFmClient | None"
    def get(self, path: str, params: Mapping[str, Any], retries: int = 3) -> dict[str, Any] | None   # headers x-api-key, Accept json, User-Agent; 404 → None; 429 → sleep(1+attempt); else ProviderError("setlistfm", "setlist.fm refused | path= status= body=", ...)
    def search_setlists(self, artist, *, date=None, year=None, page=1) -> dict | None   # /search/setlists with artistName, [date], [year], p
```

### 6.19 `adapters/onepassword.py` — unit **adapter-music**

```python
OP_PLACEHOLDER = "FILL_THIS_OUT"
def op_available() -> bool
def op_read(vault: str, item: str, field: str, runner: Runner = run, timeout_s: int = 120) -> str    # `op read op://<vault>/<item>/<field>`; ProviderError("op", ...) on failure or placeholder
def op_store_field(vault: str, item: str, field: str, value: str, runner: Runner = run, timeout_s: int = 90) -> bool   # `op item edit <item> --vault <vault> "<field>[password]=<value>"`; best effort
```

### 6.20 `adapters/openclaw.py` — unit **adapter-openclaw**

```python
class OpenClawChat:   # Chat
    def __init__(self, settings: Settings, clock: Clock, runner: Runner = run, *, urlopen: Callable = urllib.request.urlopen) -> None
    def send(self, target: ChatTarget, text: str) -> str
        # cmd = [openclaw_bin, "message", "send", "--channel", target.channel, "--target", target.chat, "-m", text] + (["--account", a]) + (["--thread-id", t]);
        # 3 tries of SEND_TIMEOUT_S=20, clock.sleep(4) between (none after the last); r.error → f"NOT SENT: openclaw message send did not run: {r.error}"; all fail → f"NOT SENT: openclaw message send failed 3 times: {tail[-200:]}";
        # ok → f"SENT to {channel} {chat}" + (f" topic {thread}" if thread is not None else "")
    def bot_token(self) -> str | None                   # openclaw_config → channels.telegram.botToken; never logged
    def rename_topic(self, chat_id: str, thread_id: str, name: str) -> str
        # token None → f"topic not renamed: no bot token ({reason})"; POST https://api.telegram.org/bot{token}/editForumTopic urlencoded chat_id, message_thread_id, name[:128], timeout 20;
        # ok → f"topic renamed to {name[:128]}"; ok False → "topic not renamed: Telegram said no"; HTTPError → f"topic not renamed: HTTP {code} {body[:120]}"; other → f"topic not renamed: {Type}"
class OpenClawContext:   # LiveContext
    def __init__(self, settings: Settings) -> None
    def live_route(self, now_s: float) -> tuple[str | None, dict[str, Any] | None]
        # sqlite3 "file:{session_db}?mode=ro" uri, timeout 2; "select current_session_id, entry_json from session_nodes" → routes.pick_live_route(rows, (now_s - context_window_s)*1000); (None, None) on any error
    def recent_text(self, now_s: float, session_id: str | None) -> str
        # ocr_last text when mtime > now_s - window; transcript_events where created_at > cutoff_ms [and session_id = ?] order by created_at desc limit 40 → routes.user_texts; "\n".join non-empty; best effort
class NullContext:   # LiveContext: (None, None) and ""
```

### 6.21 `adapters/state.py`, `adapters/clock.py` — unit **adapter-openclaw**

```python
class JsonState:   # StateStore
    def read_json(self, path, default)                 # default on missing/unreadable/invalid
    def write_json(self, path, obj, *, mode=None)      # mkdir parents; write f"{path}.tmp" with indent=2, ensure_ascii=False, trailing newline; chmod mode when given; os.replace
    def remove(self, path)                             # missing_ok
    def mtime(self, path)                              # None when missing
class SystemClock:   # Clock
    def now(self) -> datetime                          # datetime.now(timezone.utc)
    def time(self) -> float
    def sleep(self, seconds: float) -> None
```

### 6.22 `flows/services.py` (scaffold)

```python
class Services:
    def __init__(self, *, settings: Settings, calendar: Calendar, chat: Chat, context: LiveContext, state: StateStore,
                 clock: Clock, spotify: SpotifyApi | None = None, setlistfm: SetlistFmApi | None = None,
                 spotify_factory: Callable[[], SpotifyApi | None] | None = None,
                 setlistfm_factory: Callable[[], SetlistFmApi | None] | None = None)
    @property
    def spotify(self) -> SpotifyApi | None     # None = not configured; while None and a factory is set, the factory
    @property                                   # is re-run on every read (one Path.exists()), so an auth done while the
    def setlistfm(self) -> SetlistFmApi | None  # server is up takes effect on the next call; both have setters for tests
    @property
    def tz(self) -> ZoneInfo
    @property
    def operator(self) -> str
def build_services(settings: Settings | None = None) -> Services
    # settings or load_settings(); set_level(settings.log_level); SystemClock, JsonState, GwsCalendar, SpotifyClient.if_configured, SetlistFmClient.if_configured, OpenClawChat, OpenClawContext
```

### 6.23 `flows/concert_add.py` — unit **flows-calendar**

```python
ALIASES = {"artist": "title", "summary": "title", "datetime": "start", "timezone": "tz", "venue": "location", "notes": "description", "text": "source"}
KNOWN = frozenset({"title", "start", "end", "tz", "calendar", "location", "description", "attendees", "source", "allowPast", "date", "time"})
@dataclass(frozen=True)
class AddRequest:
    title: str = ""; start: str = ""; location: str = ""
    end: str | None = None; tz: str | None = None; calendar: str | None = None
    description: tuple[str, ...] = (); attendees: tuple[str, ...] = (); source: str = ""; allow_past: bool = False
    concert: bool = False; teaser: bool = True; dry_run: bool = False
    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, concert: bool = False, teaser: bool = True, dry_run: bool = False) -> "AddRequest"
        # not a dict → FixInput("input must be a JSON object", "send one {...} object"); fold ALIASES; unknown keys → FixInput(f"unknown keys: {', '.join(sorted(unknown))}", f"allowed keys: {', '.join(sorted(KNOWN - {'date','time'}))}");
        # date+time and no start → start = f"{date} {time}"; description via coerce_description; attendees via check_attendees; allowPast truthy
@dataclass(frozen=True)
class PreparedEvent:
    title: str; openers: tuple[str, ...]; start: datetime; end: datetime; end_assumed: bool; tz: str
    location: str; lines: tuple[str, ...]; dropped: tuple[str, ...]; notes: tuple[str, ...]
    attendees: tuple[str, ...]; topic: str; raw_source: str; source_norm: str | None
def prepare_event(req: AddRequest, *, context_text: str, now: datetime, settings: Settings) -> PreparedEvent
    # PURE, in legacy order: raw_source, source_norm = build_source(req.source, context_text); tz = validate_tz(req.tz or settings.tz);
    # title = squash(req.title); split_openers; concert → strip_concert_prefix; empty → need("artist" if concert else "title"); no start → need("date and time");
    # start: concert and is_date_only(start) → start_from_date (note) else parse_time; end = parse_time(end,"end") or default_end(start, default_duration_h); check_duration;
    # check_window(start, now, allow_past, past_grace_h, max_future_days); location = squash; empty → need("venue");
    # concert only: split_openers on the title; apply_show_time(start, end, end_assumed, raw_source) → notes; derived_lines(raw_source); openers = title openers or openers_from_text(raw_source);
    # plain event: title kept whole, start untouched, derived = [] (the live context belongs to the last ticket, not to a dinner added minutes later);
    # merge_lines(derived, openers, caller=req.description, source_norm, title) → lines, dropped; topic = topic_name(start, title, location)
def resolve_calendar(name: str | None, svc: Services, *, dry_run: bool) -> tuple[str, str]
    # None/"primary" → (settings.default_calendar, settings.default_calendar_name); "@" in name → (name, name); dry_run → (f"<id of {name}>", name);
    # else hits = calendars whose summary contains name (ci); len != 1 → FixInput(f"calendar {name!r} matched {n} calendars", f"use one of: {', '.join(sorted(summaries))}"); (id, summary)
def add_event(req: AddRequest, svc: Services) -> Outcome
    # never raises. now_s = svc.clock.time(); sid, route = svc.context.live_route(now_s); context = svc.context.recent_text(now_s, sid);
    # prepared = prepare_event(...); cal_id, cal_name = resolve_calendar(settings.concerts_calendar if req.concert else req.calendar, ...);
    # teaser = make_teaser(title, date, svc, dry_run) if req.concert and req.teaser else None   (never raises; status unavailable/timeout/failed)
    # setlist = find_setlist(title, date, location, svc) if req.concert else None               (never raises)
    # lines = prepared.lines + [teaser_line, setlist_line if not None]; body = render_body(title, fmt_range(start,end) + (" (end assumed)" if end_assumed else ""), location, lines, [teaser_bullet(...), setlist_bullet(...)]);
    # notes = prepared.notes + ([dropped_note(dropped)] if dropped else []);
    # common = {"calendar": cal_name, "summary": title, "start": start.isoformat(), "end": end.isoformat(), "location": location, "description": list(lines), "dropped": list(dropped),
    #           "teaser": teaser.to_json() if teaser else None, "setlist": setlist.to_json() if setlist else None, "topic": topic}
    # dry_run → Outcome(DRY_RUN, render_dry_add(cal_name, body, notes), data=common)
    # duplicate guard: existing = calendar.list_events(cal_id, time_min=start, time_max=start+1min, q=title); e = duplicate_of(existing, title);
    #   e → missing = missing_link_lines(e.description, [teaser_line, setlist_line]); missing → patch_description(patched_description(...)); finish(e.id, e.htmlLink, duplicate=True)
    # r = calendar.insert_event(...); finish(r["id"], r.get("htmlLink") or "", False)
    # finish: every concert queued: queue_add(make_entry(...), svc) when req.concert and event_id; topic: concert and settings.send and settings.rename_topics and forum_target(chat_target(route)) → svc.chat.rename_topic(chat, thread, topic)
    #         else "topic not renamed: not a Telegram forum topic" when route exists but is not a forum topic, else "topic not renamed: no live chat";
    #         Outcome(DUPLICATE|OK, render_added(calendar=cal_name, body, notes, html_link, duplicate), data={**common, "id", "htmlLink", "topic_status"})
    # exceptions: FixInput/NeedUser/ProviderError → outcome_from; anything else → crash_outcome
def add_concert(*, artist: str, start: str, venue: str, source_text: str = "", notes: str = "", svc: Services, dry_run: bool = False) -> Outcome
    # AddRequest(title=artist, start, location=venue, source=source_text, description=tuple(non-empty lines of notes), concert=True) → add_event
def add_plain(*, title: str, start: str, location: str, notes: str = "", calendar: str = "", svc: Services, dry_run: bool = False) -> Outcome
```

### 6.24 `flows/event_delete.py` — unit **flows-calendar**

```python
DELETE_ALIASES = {"artist": "title", "summary": "title", "event": "title", "name": "title", "venue": "location", "when": "date", "day": "date", "cal": "calendar", "eventId": "id", "number": "pick", "choice": "pick"}
DELETE_KNOWN = frozenset({"title", "date", "calendar", "id", "pick", "location", "tz"})
@dataclass(frozen=True)
class DeleteRequest:
    title: str = ""; date: str | None = None; calendar: str | None = None; id: str | None = None; pick: str | None = None; tz: str | None = None; dry_run: bool = False
    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, dry_run: bool = False) -> "DeleteRequest"   # unknown keys → FixInput(f"unknown keys: ...", f"use only: {', '.join(sorted(DELETE_KNOWN))}")
def find_candidates(req: DeleteRequest, svc: Services) -> list[Candidate]
    # t_min, t_max = search_window(req.date, now, tz); cals = [resolve_calendar(req.calendar)] if req.calendar else [resolve_calendar(concerts_calendar), (default_calendar, default_calendar_name)];
    # dry_run without calendar → [("<id of Concerts>", "Concerts"), (default...)] and no gws calls; each: list_events(q=title) → filter_candidates; sorted by start
def remove(c: Candidate, svc: Services, *, dry_run: bool) -> None      # calendar.delete_event then queue_forget(c.id) (errors in queue_forget swallowed)
def delete_event(req: DeleteRequest, svc: Services) -> Outcome
    # candidates file older than context_window_s → removed; pick (or bare-number title) then → NO_CANDIDATES_REPLY
    # NEW: pick None and looks_like_pick(title) and a fresh candidates file exists → pick = title
    # pick → n = pick_index; cands = state.read_json(candidates_file, []); bad n/out of range → Outcome(NEED_USER, NO_CANDIDATES_REPLY, error="no such candidate");
    #        remove; state.remove(candidates_file); Outcome(DRY_RUN|OK, render_removed(c, dry), data={"deleted": not dry, **c.to_json()})
    # id → cal = resolve_calendar(req.calendar or concerts_calendar); e = get_event; c = candidate_from_event; remove; same Outcome
    # empty title → Outcome(NEED_USER, WHICH_EVENT_REPLY, error="title is required")
    # found = find_candidates; 0: dry → Outcome(DRY_RUN, render_would_look(...)); else Outcome(OK, render_none_found(...), data={"deleted": False})
    # 1: remove → Outcome; n: state.write_json(candidates_file, [c.to_json()]); Outcome(OK, render_candidates(title, found), data={"deleted": False, "candidates": [...]})
    # exceptions as in add_event
```

### 6.25 `flows/queue.py` — unit **flows-calendar**

```python
@dataclass(frozen=True)
class QueueEntry:
    event_id: str; calendar_id: str; artist: str; date: str; venue: str; setlistfm_url: str | None; song_count: int; until: str; added: str
    def to_json(self) -> dict[str, Any]
    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "QueueEntry"
@dataclass(frozen=True)
class QueueStatus: entries: tuple[QueueEntry, ...]; active: tuple[QueueEntry, ...]; expired: tuple[QueueEntry, ...]; path: Path
def make_entry(*, event_id: str, calendar_id: str, artist: str, start: datetime, venue: str, setlist: SetlistResult | None, now: datetime, days: int) -> QueueEntry
    # until = (start + days).strftime("%Y-%m-%d"); added = now.isoformat(timespec="seconds"); setlistfm_url/song_count from setlist when found
def queue_list(svc: Services) -> list[QueueEntry]                       # read_json(settings.setlist_queue, []); bad rows skipped
def queue_add(entry: QueueEntry, svc: Services) -> bool                 # False when event_id present; writes with indent 2 (legacy shape, a JSON list)
def queue_forget(event_id: str, svc: Services) -> bool
def queue_status(svc: Services) -> QueueStatus                          # split by until >= today (in settings.tz); sorted by date
def queue_outcome(svc: Services) -> Outcome                             # Outcome(OK, render_queue(status), data={"count", "entries"})
```

### 6.26 `flows/teaser.py` — unit **flows-music**

```python
def resolve_artist(name: str, api: SpotifyApi) -> tuple[Artist, bool]   # search_artists(name, 10) → pick_artist; log_warn("artist name corrected | asked= spotify=") when corrected
def liked_by_artist(artist: Artist, api: SpotifyApi, *, deadline: float | None = None) -> list[dict[str, Any]]   # liked_hits(api.liked_rows(deadline=deadline)); empty → one forced refresh
def make_teaser(artist: str, date: str, svc: Services, *, dry_run: bool = False, description: str | None = None) -> TeaserResult
    # svc.spotify None → status "unavailable"; date = concert_date(date) (FixInput propagates: the caller's input); deadline = clock.time() + teaser_timeout_s;
    # try: artist resolved; tracks; name = playlist_name(date, artist.name); existing = find_playlist_named(my_playlists(), name);
    #   no tracks → "none"; dry_run → "dry_run" (exists); existing → top up (new_uris vs playlist_items) → "updated"; else create_playlist(name, description or teaser_description, public=False) + add_items → "created"
    # except ProviderError as e: "timeout" when e.provider == "spotify-timeout" else "failed" with reason=str(e)[:160]; NoArtistMatch → "failed" reason=str(e); any other Exception → "failed" reason=f"{type(e).__name__}: {e}"[:160]
def teaser_outcome(artist: str, date: str, svc: Services, *, dry_run: bool = False) -> Outcome
    # standalone tool: empty artist → need("artist"); empty date → need("date"); res = make_teaser; unavailable → Outcome(ERROR, provider_reply("spotify-unconfigured", op), error=...);
    # else Outcome(OK|DRY_RUN, render_teaser_standalone(res, timeout_s, dry), data=res.to_json())
```

### 6.27 `flows/setlist.py` — unit **flows-music**

```python
def find_setlist(artist: str, date: str, venue: str | None, svc: Services) -> SetlistResult
    # svc.setlistfm None → match "unavailable"; date = concert_date(date); rows = [setlist_row(s) for s in search_setlists(artist, date=setlistfm_date(date)).setlist]; rank_by_venue;
    # empty → search_setlists(artist, year=yyyy) → near_date → "within a day" or "none" (search_url); found → SetlistResult(found=True, match, setlist=rows[0]);
    # ProviderError → "failed" with reason; never raises except FixInput from concert_date
def setlist_outcome(artist: str, date: str, venue: str, svc: Services) -> Outcome
    # empty artist/date → need; unavailable → Outcome(ERROR, provider_reply("setlistfm-unconfigured", op)); else Outcome(OK, render_setlist_standalone(res), data=res.to_json())
```

### 6.28 `flows/playlists.py`, `flows/auth.py` — unit **flows-music**

```python
# playlists.py: spotify.py's CRUD kept for the CLI; each returns the legacy JSON record (dict); require_spotify raises ProviderError("spotify-unconfigured", ...)
def require_spotify(svc: Services) -> SpotifyApi
def me(svc) -> dict; def liked(svc, *, artist: str | None = None, refresh: bool = False) -> dict; def list_playlists(svc) -> dict; def playlist_tracks(playlist_id, svc) -> dict
def create_playlist(name, svc, *, description="", public=False, dry_run=False) -> dict      # refuses an existing name (created False + playlist_row)
def add_tracks(playlist_id, uris, svc, *, dry_run=False) -> dict                             # validate_track_uris; skips present; 100 per POST
def rename_playlist(playlist_id, name, svc, *, dry_run=False) -> dict                        # owner check via svc.spotify.user_id; idempotent
def unfollow_playlist(playlist_id, svc, *, dry_run=False) -> dict
def search_artist(name, svc) -> dict

# auth.py: interactive, CLI only, may print to stdout
def spotify_auth(settings: Settings, state: StateStore, *, manual: bool = False, force: bool = False, open_browser: Callable[[str], Any] = webbrowser.open, runner: Runner = run) -> dict[str, Any]
    # legacy cmd_auth: refuse when the file exists and not force; client id/secret from op (op_read username/credential; placeholder refused) or getpass (32 chars each);
    # local HTTPServer on redirect uri, 300 s, state check; authorization_code → tokens; save (600); /me → user_id; op_store_field("refresh token") best effort; returns {"ok", "user", "user_id", "scope", "credentials"}
def setlistfm_auth(settings: Settings, state: StateStore, *, manual: bool = False, force: bool = False, runner: Runner = run, urlopen=urllib.request.urlopen) -> dict[str, Any]
    # key from op or getpass; len >= 20 and not placeholder; save (600); probe one search; returns {"ok", "config", "probe_total"}
```

### 6.29 `flows/deliver.py` — unit **flows-deliver-health**

```python
def deliver(outcome: Outcome, svc: Services, *, send: bool) -> Delivery
    # no reply → Delivery(False, "NOT SENT: no reply"); kind FIX → "NOT SENT: fix"; DRY_RUN → "NOT SENT: dry run"; not send → "NOT SENT: delivery disabled";
    # sid, route = context.live_route(now); route None → "NOT SENT: no live chat (CLI run)"; target = chat_target(route); None → "NOT SENT: no chat target in the route";
    # last = state.read_json(sent_log, {}); same text within dedupe_window_s → "NOT SENT: identical reply was sent less than 10 minutes ago";
    # status = chat.send(target, reply); startswith "SENT" → write sent_log {"text", "at"} → Delivery(True, status); else Delivery(False, status)
def render_tool_result(outcome: Outcome, delivery: Delivery, *, tool: str | None = None) -> str
    # FIX → "FIX: " + fix + (f" Call {tool} once more with only that change." if tool else ""); delivery.sent → NO_REPLY_TEXT; reply → f"{reply}\n{delivery.status}"; else json.dumps(outcome.to_json(), ensure_ascii=False)
def fix_guard(outcome: Outcome, svc: Services, tool: str) -> Outcome
    # log = state.read_json(fix_log, {}); times = [t for t in log.get(tool, []) if now - t < context_window_s];
    # FIX → times.append(now); write; len(times) >= 3 → Outcome(NEED_USER, reply=FIX_GUARD_REPLY[kind], error="repeated FIX") and clear the list;
    # non-FIX → clear the list; return outcome
FIX_GUARD_ADD = "I could not get the details right from that after a few tries. Reply with the artist, date, show time and venue in one line and I will add it."
FIX_GUARD_DELETE = "I could not work out which event you mean after a few tries. Reply with the event name and date in one line and I will look again."
FIX_GUARD_OTHER = "I could not get that right after a few tries. Reply with the artist and the date as YYYY-MM-DD and I will try once more."
def run_tool(fn: Callable[[], Outcome], svc: Services, *, tool: str | None = None, send: bool | None = None, guard: bool = False) -> tuple[Outcome, Delivery, str]
    # outcome = fn() (crash → crash_outcome); guard → fix_guard; delivery = deliver(outcome, svc, send=settings.send if send is None else send); text = render_tool_result(outcome, delivery, tool=tool)
```

### 6.30 `flows/healthcheck.py` — unit **flows-deliver-health**

```python
CHECK_NAMES = ("python", "mcp-sdk", "config-dir", "state-dir", "gws-binary", "gws-keyfile", "gws-auth", "concerts-calendar",
               "spotify-credentials", "spotify-auth", "liked-cache", "setlistfm-key", "setlistfm-auth",
               "openclaw-binary", "openclaw-config", "openclaw-session-db", "setlist-queue")
def run_healthcheck(settings: Settings, *, probe: bool = False, svc: Services | None = None, only: Sequence[str] = ()) -> HealthReport
    # python: PASS f"{version}" / FAIL < 3.11 (getting-started). mcp-sdk: import mcp → PASS version / FAIL "pip: mcp>=2,<3" (getting-started).
    # config-dir/state-dir: read-only → PASS path when writable, PASS f"{path} absent (created on first write)" under a writable ancestor / FAIL "not writable" (configuration).
    # gws-binary: which/exists → PASS path / FAIL "install the gws CLI, or set CONCERT_MCP_GWS_BIN to its path" (google-calendar). gws-keyfile: exists and mode 600 → PASS "mode 600" / FAIL "missing", fix KEYFILE_FIX ("create the file keyring key: GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file gws auth login") or f"mode {oct}, want 600" (google-calendar).
    # gws-auth: probe → calendar.list_calendars() within gws_timeout_s → PASS f"{n} calendars" / FAIL error with fix "gws auth login", or KEYFILE_FIX for ProviderError("gws-keychain") / SKIP "pass --probe". concerts-calendar: SKIP "gws-auth failed" when that row failed in the same run; else probe → resolve → PASS id-less f"found {name}" / FAIL fix text.
    # spotify-credentials: exists, mode 600, keys client_id/client_secret/refresh_token → PASS (evidence "LEGACY path in use: run concert-mcp migrate" when field in legacy_in_use) / FAIL "run concert-mcp spotify auth" (spotify).
    # spotify-auth: probe → /me → PASS display_name / FAIL. liked-cache: PASS f"{age}" / SKIP "absent: first teaser will be slow (~40 s)".
    # setlistfm-key: exists, 600, api_key → PASS / FAIL "run concert-mcp spotify setlist-auth" (setlistfm). setlistfm-auth: probe one search.
    # openclaw-*: settings.send False → SKIP "delivery off"; binary PASS/FAIL (openclaw); config: botToken present → PASS "telegram bot token present (topic rename possible)" / SKIP "no bot token: topics are not renamed"; session-db: readable sqlite → PASS (+ age of newest route) / SKIP "absent: replies are not delivered".
    # setlist-queue: PASS f"{n} shows queued". Never raises; an exception inside a check becomes FAIL with the exception type.
def healthcheck_outcome(settings: Settings, *, probe: bool, svc: Services | None) -> Outcome   # Outcome(OK if report.ok else ERROR, reply=render_health(report), data=report.to_json())
```

### 6.31 `server.py` — unit **server**

```python
INSTRUCTIONS: str                                   # §4, < 2000 chars; a test asserts length and that it contains "NO_REPLY" and "FIX:"
def build_server(svc: Services | None = None) -> MCPServer
    # MCPServer("concert", instructions=INSTRUCTIONS); svc built lazily on first tool call (so tools/list needs no credentials); registers the seven tools
def concert_add(artist: str = "", start: str = "", venue: str = "", source_text: str = "", notes: str = "") -> str
def calendar_add(title: str = "", start: str = "", location: str = "", notes: str = "", calendar: str = "") -> str
def event_delete(title: str = "", date: str = "", pick: str = "") -> str
def teaser_playlist(artist: str = "", date: str = "") -> str
def setlist_find(artist: str = "", date: str = "", venue: str = "") -> str
def queue_status() -> str
def healthcheck() -> str
def main() -> None                                  # build_server().run() over stdio; set_level from settings; nothing but JSON-RPC on stdout
```

Each tool: `run_tool(lambda: flow(...), svc, tool=name, guard=True)` → return text; the arguments arrive cleaned by
`Param`'s `BeforeValidator(clean_param)`. The parameters carry `Annotated[Param, Field(description=...)]` so the schema
has descriptions; the SDK import is the only one outside tests. Every mutating tool passes `dry_run=settings.dry_run` (`CONCERT_MCP_DRY_RUN`, default off), so a whole bot conversation can be rehearsed with nothing written; the CLI also has `--dry-run`.

### 6.32 `cli.py` — unit **cli**

```
concert-mcp [--json] [--dry-run] [--no-send] [--config-dir PATH] <subcommand>
  serve                                    MCP stdio server (what OpenClaw launches); also the default with no subcommand
  add [--concert] [--no-teaser] [--title --start --location --end --tz --calendar --description* --attendees* --source --allowPast] [JSON]
  del [--title --date --calendar --id --pick] [JSON]          (alias: delete)
  teaser --artist A --date D [--description T]
  setlist-find --artist A --date D [--venue V] [--songs]
  queue
  healthcheck [--probe] [--only NAME]*     exit = number of FAIL rows
  config                                   effective settings, redacted, EXISTS/MISSING/LEGACY per path
  migrate                                  copy the legacy ari-spotify files into the concert-mcp dirs (never deletes)
  spotify {auth [--force --manual] | me | liked [--artist --refresh] | playlists | playlist-tracks --playlist | playlist-create --name [--description --public] |
           playlist-add --playlist --uri* | rename --playlist --name | playlist-unfollow --playlist | search-artist --name | setlist-auth [--force --manual]}
```

```python
def build_parser() -> argparse.ArgumentParser
def read_request(args: Sequence[str], stdin: TextIO, *, known: frozenset[str], aliases: Mapping[str, str], list_keys: frozenset[str] = frozenset({"description", "attendees"})) -> dict[str, Any]
    # legacy parse: flags (--key value; unknown → FixInput(f"unknown flag --{key}", f"allowed flags: {', '.join('--'+k for k in sorted(known - {'date','time'}))}"); missing value → FixInput(f"--{key} needs a value", f"pass a value after --{key}"));
    # list_keys repeat; else one JSON object from the positional or stdin (FixInput("input is not valid JSON: {e}", <per-command hint>)); aliases folded
def main(argv: Sequence[str] | None = None) -> int
    # add/del/teaser/setlist-find/queue/healthcheck: outcome via flows → run_tool(send=not --no-send, guard=False) → print text (or json record with "sent") → return exit_code;
    # healthcheck returns report.failures; spotify/* print the legacy JSON record, exit 1 on ProviderError (message on stderr via log_err); never prints a traceback
```

## 7. Tests

Principles: pure modules against literal strings copied from the legacy scripts; flows end-to-end on `tests/fakes.py`
with exact reply goldens and recorded adapter calls; adapters on a `FakeRunner`/`FakeUrlopen`; one real-process stdio
smoke test; a docs test. No test touches the network, the keychain, `~`, gws or openclaw; `conftest` points every
`CONCERT_MCP_*_DIR` at `tmp_path` and sets `CONCERT_MCP_SEND=0` unless the test is about delivery.

`tests/fakes.py` (scaffold): `FakeCalendar(calendars, events, fail=None)` with `calls`, `inserted`, `patched`,
`deleted`, `seed_event()` (htmlLink `https://www.google.com/calendar/event?eid=<b64>`); `FakeSpotify(artists, liked,
playlists, items, fail=None)` with `created`, `added`, `renamed`, `unfollowed`; `FakeSetlistFm(responses, fail=False)`
keyed `(artist, date, year)`; `FakeChat(fail_sends=0, rename_status="topic renamed to {name}")` with `sent`,
`renamed`; `FakeContext(route=None, session_id=None, text="")`; `MemoryState`; `FakeClock(now)` with `slept`,
`advance()`; `FakeRunner({argv-prefix: (rc, out, err) | callable})` recording `calls`, raising `AssertionError` on an
unknown argv; `FakeUrlopen({(method, url-prefix): response | HTTPError | callable})` recording requests.

`conftest.py` (scaffold): `now` = 2026-10-25T12:00:00-07:00, `settings(tmp_path)`, `svc` = Services on fakes with a
Concerts calendar seeded, `ticket_text` (fictional Ticketweb OCR with `Doors 7:30 PM`, `Show 8:30 PM`, `2 tickets`,
`$42.00`, a URL, `with special guest Zork.`), `forum_route` (telegram, chat `-1001234567890`, thread `7`).

Per unit (the unit owns these files):

- **text-parse**: `test_dates.py` (all eight formats, `Z`, naive→tz, garbage → exact FIX text; `is_date_only`; clock forms; show_time certain/inferred/None; `apply_show_time` moves an assumed end and emits the note; inferred mismatch → the "doors are earlier" FIX; `check_window` past/allow_past/far future with exact replies; `start_from_date`; `fmt_range` same/cross day; `concert_date`; `when_of` all-day). `test_titles.py` (split_openers for with/w/ /+/featuring/feat./support from, lists with , and & /; head < 2 chars untouched; strip_concert_prefix; title_matches rules; normalize_name table; clean_param None/null/quotes/spaces; looks_like_pick). `test_derive.py` (derived_lines Doors normalisation and Purchased singular/plural/amount/platform; openers_from_text own-line only; verify_line claim rules incl. PLAIN_RE and None source; merge_lines precedence: derived Doors beats caller `Doors: 9 PM`, opener equal to title dropped, dropped order; coerce_description literal `\n`; check_attendees).
- **text-render**: `test_naming.py`; `test_reply.py` (bullets key rule; **`test_event_link_is_eventedit_form`**: `https://www.google.com/calendar/event?eid=abc123` → `https://calendar.google.com/calendar/u/0/r/eventedit/abc123`, no eid → unchanged, None → None, with a comment naming the 2026-10-25 phone test; render_added duplicate vs new; render_dry_add; teaser_bullet/setlist_bullet every status; render_removed with a teaser line; render_candidates numbering; none_found/would_look wording with configured names; render_queue; provider_reply per provider with operator). `test_candidates.py` (filter drops cancelled, search_window with/without date and the FIX, duplicate_of ci, missing_link_lines/patched_description, teaser_url_in, pick_index "2." ).
- **text-music**: `test_artists.py` (pick_artist exact-by-followers, word-subset `zed fox` ~ `Zed Fox & Friends` corrected=True, `ZZZ Test Band` vs `Band of Horses` → NoArtistMatch with legacy message; liked_hits by id then name; new_uris; playlist_row items/tracks; TeaserResult.to_json legacy keys). `test_setlists.py` (setlist_row date flip + songs; rank_by_venue stable; near_date; search_url quoting; SetlistResult.to_json). `test_routes.py` (pick_live_route newest-with-route, ignores old/no-route/garbage; chat_target strips `telegram:` and `:topic:n`; forum_topic refusals; user_texts str and list content).
- **adapter-gws**: `test_gws.py` (env sets the file backend only when the key file exists; keychain guard on darwin → ProviderError with the keychain reply, not on linux; exact `+insert` argv incl. `--description`/`--attendee`; events list params; patch `--json`; delete runs with `-o gws-del.out` in a temp cwd and leaves nothing; rc≠0 → ProviderError("gws") with the refused reply; missing binary → "gws-missing"; bad JSON → "gws-nojson").
- **adapter-music**: `test_spotify_http.py` (refresh on expiry persists rotated token with mode 600; 429 honours Retry-After capped 30 via clock.sleep; 401 refreshes once; other HTTPError → ProviderError with status; paged follows next; deadline → "spotify-timeout"; liked cache TTL, old cache without artist_ids refetched, sorted oldest first; create_playlist POSTs /me/playlists; add_items chunks of 100; if_configured None). `test_setlistfm_http.py` (headers, 404 → None, 429 then 200, refused → ProviderError, load/save key 600, if_configured). `test_onepassword.py` (argv, placeholder refused).
- **adapter-openclaw**: `test_openclaw.py` (send argv with --account/--thread-id; 3 tries with two 4 s sleeps; "did not run"; "failed 3 times" tail; SENT string with/without topic; bot_token; rename_topic: no token, ok True, ok False, HTTP 400 body[:120], URLError type; live_route from a fixture sqlite with `session_nodes(current_session_id, entry_json)`; recent_text from `transcript_events(session_id, created_at, event_json)` + fresh/stale OCR file; missing db → (None, None)/""). `test_state.py` (atomic write, mode, default on corrupt, mtime).
- **flows-calendar**: `test_flow_concert_add.py` (happy path golden reply byte-for-byte incl. `(end assumed)`, Doors/Purchased/Opener/Teaser/Setlist bullets and the eventedit link; FakeCalendar.inserted argv-equivalent fields; description order; queue entry until=+61 d; FakeChat.renamed topic `2026-11-14 | Zed Fox | The Quarry`; duplicate → no insert, patch with only missing lines, `**Already on Concerts**`; dry_run → no writes, `**Would add to Concerts**`; doors-as-start → FIX; certain show time corrects start + note; date-only start taken from text / need("show time"); past/far-future replies; empty title → need artist (concert) / title (plain); empty venue; unknown key FIX; description str with `\n`; spotify None → "not made (Spotify is not set up…)"; FakeSpotify(fail) → "not made (…)" and event still inserted; setlist 404 → "not on setlist.fm yet…" and queued; FakeCalendar(fail="insert") → ERROR with the gws reply and nothing queued; add_plain → default calendar, no teaser/setlist/queue/rename, title `[Dinner] ZZZ Bistro` untouched; send=False → "topic not renamed: no live chat"; non-forum route → "not a Telegram forum topic"). `test_flow_event_delete.py` (one match deletes + queue_forget + golden; teaser left-in-place row; two matches → candidates file + numbered golden; pick "2" and "2." delete the right one and clear; stale pick → NO_CANDIDATES_REPLY; title "2" with a file → pick; by id; none found with/without date; bad date FIX; empty title; dry-run both branches; word matching `zed fox` vs `ZED FOX & Friends`). `test_flow_queue.py` (idempotent add, forget, status split, render).
- **flows-music**: `test_flow_teaser.py` (created oldest first; updated adds only new uris; none; dry_run; unavailable; NoArtistMatch → failed reason; timeout via FakeClock; teaser_outcome replies incl. unconfigured and need). `test_flow_setlist.py` (exact; within a day with venue word; none → search_url; unavailable; failed; setlist_outcome). `test_flow_playlists.py` (each CRUD record shape; create refuses duplicate; add skips present; rename owner check; require_spotify).
- **flows-deliver-health**: `test_deliver.py` (each NOT SENT status; dedupe within 600 s then allowed after; SENT writes the log; render_tool_result all four shapes incl. the appended sentence only with `tool`; fix_guard: two FIX pass through, third within window → the guard reply and cleared, success clears, stale pruned; run_tool crash → crash_outcome reply). `test_healthcheck.py` (bare tmp → gws-keyfile, spotify-credentials, setlistfm-key FAIL with anchors present in `ANCHORS`; probe=False → auth rows SKIP; send=False → openclaw rows SKIP; mode 644 → `mode 0o644, want 600`; legacy-only spotify file → PASS with the migrate hint; probe=True on fakes → PASS; failures == FAIL count; render matches `^(PASS|FAIL|SKIP)  \S+\s+.+$`). `test_health_render.py`.
- **server**: `test_server.py` (build_server with fake svc: list of tool names exactly the seven; every property type string with a non-empty description and default; INSTRUCTIONS < 2000 chars; calling `concert_add` with `start="tomorrow"` returns a string starting `FIX: start`; `healthcheck` returns the table; a flow raising RuntimeError returns the crash reply, no exception). `test_smoke_stdio.py` (spawns `sys.executable -m concert_mcp serve` with tmp env and `CONCERT_MCP_SEND=0` through the mcp stdio client; initialize instructions contain NO_REPLY; list_tools → seven names; call healthcheck → text contains `FAIL  gws-keyfile`, isError False; skipped when `mcp` is not importable).
- **cli**: `test_cli.py` (`add --concert --dry-run --no-send --json` flags == JSON stdin == aliases; record keys; `--bogus` → `FIX: unknown flag --bogus` exit 1; `--title` without value; `del --pick 2`; `healthcheck` exit == failures; `config` never prints a secret value; `migrate --dry-run`; `queue --json`; `spotify playlists` legacy shape; stdout is exactly one block, logging on stderr; no subcommand → serve dispatched (monkeypatched)). `test_no_stdout_in_library.py` (grep `print(` in `src/` outside `cli.py`, `flows/auth.py`).
- **docs**: `test_readme.py` (every `ANCHORS` value's heading exists in README.md; every `env_table()` name appears in README.md; the OpenClaw snippet's JSON parses; `docs/homelab-integration.md` contains `check_concert_mcp` and `fix_concert_mcp`).
- **optional**: `test_legacy_parity.py` (skipped unless `CONCERT_MCP_LEGACY_DIR` names a dir with `cal-add.py`): for 8 inputs run `python3 cal-add.py --concert --dry-run --no-send --json --no-teaser` with `HOME=tmp` and compare reply/summary/start/end/description/dropped/topic/fix with `add_event(dry_run=True, teaser=False)` on FakeContext(text="") and setlistfm None. Operator name set to the legacy word for the comparison.

CI (`.github/workflows/ci.yml`): `lint` (ruff check, ruff format --check), `test` matrix 3.11/3.12/3.13
(`astral-sh/setup-uv@v5`, `uv sync --dev` or `--locked --dev` once `uv.lock` exists, `uv run pytest -q`),
`bare-healthcheck` (`uv run concert-mcp healthcheck; test $? -ge 3` and grep `README.md#`).

## 8. Build units and order

Every unit owns a disjoint set of files (source + its tests). The scaffold runs first and alone; it writes the
foundations in full and every other file as a stub with the exact signatures above, so `import concert_mcp.server`
succeeds from minute one. Then the units run in parallel.

| Unit | Files | Depends on (signatures only, from the scaffold) |
|---|---|---|
| scaffold | pyproject, LICENSE, .gitignore, CI, bin/healthcheck, README outline, all `__init__.py`, config.py, log.py, proc.py, result.py, adapters/protocols.py, flows/services.py, tests/conftest.py, tests/fakes.py, every other file as a stub | nothing |
| text-parse | text/dates.py, text/titles.py, text/derive.py, test_dates/titles/derive | result |
| text-render | text/naming.py, text/reply.py, text/candidates.py, test_naming/reply/candidates | result, text/dates (fmt_range), artists/setlists result types (stubs carry the dataclasses in full) |
| text-music | text/artists.py, text/setlists.py, text/routes.py, test_artists/setlists/routes | result, titles.normalize_name |
| adapter-gws | adapters/gws.py, test_gws | proc, result, reply.provider_reply |
| adapter-music | adapters/spotify_http.py, adapters/setlistfm_http.py, adapters/onepassword.py, their tests | proc, result, protocols, text/artists |
| adapter-openclaw | adapters/openclaw.py, adapters/state.py, adapters/clock.py, test_openclaw, test_state | proc, protocols, text/routes |
| flows-calendar | flows/concert_add.py, flows/event_delete.py, flows/queue.py, their tests | text/*, protocols, flows/teaser+setlist signatures |
| flows-music | flows/teaser.py, flows/setlist.py, flows/playlists.py, flows/auth.py, their tests | text/artists, text/setlists, protocols, onepassword |
| flows-deliver-health | flows/deliver.py, flows/healthcheck.py, text/health.py, test_deliver, test_healthcheck, test_health_render | result, protocols, text/routes, reply |
| server | server.py, test_server, test_smoke_stdio | flows signatures, deliver.run_tool |
| cli | cli.py, __main__.py, test_cli, test_no_stdout_in_library | flows signatures, deliver.run_tool, config |
| docs | README.md body, CHANGELOG.md, CONTRIBUTING.md, docs/homelab-integration.md, docs/migration.md, .github/ISSUE_TEMPLATE/bug.md, test_readme | config.env_table names, health.ANCHORS |

The scaffold writes the value dataclasses (`TeaserResult`, `SetlistResult`, `Candidate`, `Artist`, `Setlist`,
`ChatTarget`, `QueueEntry`, `QueueStatus`, `Check`, `HealthReport`, `AddRequest`, `DeleteRequest`, `PreparedEvent`,
`SplitTitle`, `RunResult`) **in full** inside their stub modules, with only functions and methods left as
`NotImplementedError`, so units that consume them can type against the real shapes.

Integration (after every unit lands): `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`, then
on the laptop `uv run concert-mcp healthcheck --probe`, `uv run concert-mcp add --concert --dry-run --no-send --title
'ZZZ Test Band' --start '2026-11-20 20:00' --location 'ZZZ Hall'`, `openclaw mcp doctor concert --probe`,
`openclaw mcp tools | grep concert__`, then one real ticket in a Telegram topic: one message, NO_REPLY from the model,
topic renamed, the eventedit link opens the event on the phone.

## 9. pyproject essentials

```toml
[project]
name = "concert-mcp"; version = "0.1.0"; description = "Concert tools for a small-model Telegram bot: calendar, Spotify teaser, setlist.fm, as one MCP server and CLI"
readme = "README.md"; license = "MIT"; requires-python = ">=3.11"; authors = [{ name = "Ari Sweedler" }]
dependencies = ["mcp>=2,<3"]
[project.scripts] concert-mcp = "concert_mcp.cli:main"
[dependency-groups] dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6"]
[build-system] requires = ["hatchling"]; build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel] packages = ["src/concert_mcp"]
[tool.ruff] line-length = 120; target-version = "py311"
[tool.ruff.lint] select = ["E", "F", "I", "UP", "B", "SIM"]
[tool.pytest.ini_options] testpaths = ["tests"]; asyncio_mode = "auto"
```

## 10. Deliberate wording changes from the legacy scripts (CHANGELOG)

- `Ari:` → `{operator}:` (`CONCERT_MCP_OPERATOR_NAME`, default `Admin`); `on the laptop` → `on the host`; `The laptop keychain` → `The login keychain`.
- Teaser `not made (ari-spotify skill is not on this machine)` → `not made (Spotify is not set up: run \`concert-mcp spotify auth\`)`; the Spotify failure text is `str(ProviderError)[:160]` instead of the last stderr line (same content, no ANSI).
- Setlist: a missing setlist.fm key yields `lookup skipped (setlist.fm is not set up: …)` instead of silently omitting the bullet (the legacy only omitted it when the whole skill was absent).
- Teaser description: `every song Ari liked by them, in the order he liked them` → `every liked song by them, in the order they were liked`.
- `on Concerts and Personal` / `on Concerts or Personal` use the configured calendar names.
- FIX lines on the MCP path gain ` Call <tool> once more with only that change.`; the CLI prints the legacy line.
- New, additive: `clean_param`, bare-number title as pick, date-only start from the ticket text, two no-space time formats, `fix_guard`, `queue_status`, `healthcheck`, `calendar_add` as a named tool.
- The setlist.fm `User-Agent` is `concert-mcp/<version> (personal concert bot)`; the 1Password item default is `Spotify - concert-mcp`.
- The delete candidates list and the reply dedupe log moved from `~/.openclaw/media` to the state directory (`delete-candidates.json`, `last-sent.json`); both expire within minutes, so there is no fallback.

## 11. Risks

1. **mcp 2.x API surface.** `from mcp.server.mcpserver import MCPServer`, `@tool()`, `run()`, and the client imports in the smoke test are the only SDK touches (server.py, test_server.py, test_smoke_stdio.py). If the installed 2.x differs, those three files change and nothing else.
2. **Package installs refused on the build machine.** Everything but `mcp` is stdlib; `text/`, `adapters/`, `flows/` and their tests run without `mcp` installed; `server.py` is imported lazily by `cli.py`; the smoke test skips. Ship `pyproject.toml` without `uv.lock` and say so.
3. **requestTimeoutMs.** A cold `concert_add` is 60–150 s, and about 8 min when every provider is at its cap (teaser 150 s, two setlist.fm requests at 30 s, two gws calls at 60 s, three sends at 20 s, a 20 s rename); OpenClaw's default 60 s would cut it off after the insert and before the reply. The snippet sets 480000; the duplicate guard makes a retry safe.
4. **gws keychain deletion on macOS.** Preserved guard: refuse gws without the file backend when the login keychain is unreachable; healthcheck FAILs on a missing or 644 key file.
5. **OpenClaw internals** (sqlite schema, `openclaw.json` token path, `ocr-last.txt`, `message send` flags) are undocumented. All reads are best effort in `adapters/openclaw.py` + `text/routes.py`; failure degrades to `NOT SENT: no live chat` and empty context (which drops every claim line, the safe direction).
6. **Telegram bot token** is read for the rename only; never logged, never in a reply, never in the health table (a test asserts).
7. **stdout discipline.** One stray `print()` corrupts the MCP stream; `test_no_stdout_in_library.py` greps, and all logging is stderr.
8. **Shared state files** (queue, candidates, sent log, fix log) between the server and CLI runs: atomic `os.replace` writes; dedupe and the guard are advisory.
9. **Queue path migration.** The weekly poller reads the legacy queue path; the fallback keeps it working until the first write creates the new file, so the poller must be repointed (or `CONCERT_MCP_SETLIST_QUEUE` set to the legacy path) at the same time. `docs/migration.md` says so.
10. **Timezone.** One zone (`CONCERT_MCP_TZ`) unless `tz` is passed per request; the model never supplies it.
11. **Python 3.15 on the hosts, 3.11–3.13 in CI.** No PEP 695 syntax, no `type` statements, no `datetime.utcnow`.
12. **Seven tools for a 9B model.** The documented entry filters to the three chat jobs (`toolFilter.include`); the four operator tools are the CLI's and are exposed only by an entry without the filter.
13. **Personal data.** `tests/test_readme.py` scans every text file in the repository (source, tests, docs, workflows, lock file) for email addresses outside `example.com`, Telegram chat ids (`-100\d{9,}`) other than the one fictional fixture, bot tokens, Google `group.calendar` ids and `.local` hostnames; CI runs that test on its own as the `personal-data` job, so there is one pattern list.
