# concert-mcp

One MCP server and CLI for the concert stack behind a small-model Telegram bot. A 9B local model behind an OpenClaw Telegram bot turns a ticket screenshot into a Google Calendar event, a Spotify teaser playlist, a setlist.fm lookup and a weekly setlist check, and renames the Telegram topic to `YYYY-MM-DD | Artist | Venue`. The same flows run from the command line, so a developer can test every step without an MCP client and the old dotfiles scripts become one-line wrappers. Credentials stay on the host in XDG directories; nothing in this repository names a person, a machine, a calendar or a chat. The rule that shapes everything: **the model supplies a few plain strings (artist, date, venue); code owns every policy, default, validation, idempotency guard, side effect and the exact reply text.**

## Architecture

```
phone --photo--> Telegram --> OpenClaw gateway (laptop) --stdio--> `concert-mcp serve`
                                  |  the 9B model picks one tool           |
                                  |  and passes artist, start, venue       +-- gws CLI -------------> Google Calendar
                                  |                                        +-- urllib ---------------> Spotify Web API
                                  |                                        +-- urllib ---------------> setlist.fm API
                                  <---- `openclaw message send` -----------+   the reply, delivered by code
                                  <---- Bot API editForumTopic ------------+   the topic rename
```

Three layers, each importable without the one above it:

- `text/`: pure functions. No I/O, no clock, no environment. Date parsing, title and opener split, the derived Doors/Purchased lines, the hallucination filter, playlist naming, every reply string, candidate matching, the health table.
- `adapters/`: one class per side effect, each behind a `Protocol` so tests use fakes: `GwsCalendar` (subprocess), `SpotifyClient` and `SetlistFmClient` (urllib), `OpenClawChat` and `OpenClawContext` (subprocess, sqlite, Bot API), `JsonState`, `SystemClock`.
- `flows/`: the policy. `concert_add`, `event_delete`, `queue`, `teaser`, `setlist`, `playlists`, `auth`, `deliver`, `healthcheck`. A flow returns an `Outcome`; it never prints and never exits. `server.py` (MCP over stdio) and `cli.py` are thin shells over the same flows.

The output contract, which every tool and every CLI subcommand ends in:

1. A flow returns `Outcome(kind, reply, error, fix, data)`.
2. `deliver()` sends the reply itself through `openclaw message send` when a chat spoke in the last 15 minutes and the same text was not sent in the last 10; otherwise the status is `NOT SENT: <why>`.
3. Sent: the tool result is the `NO_REPLY` block, and the model's whole answer is `NO_REPLY`.
4. Not sent: the tool result is the reply text plus the `NOT SENT` line, and the model relays the text unchanged.
5. The caller's input was wrong: `FIX: <what to change>`, the only retry signal. On the MCP path it ends with `Call <tool> once more with only that change.`; the third FIX for one tool within 15 minutes becomes a question to the user instead.

The seven tools. Every parameter is a string with default `""`; a missing required value is answered in code (`I could not read the artist from that. Reply with it and I will add the event.`), never by a schema error:

| Tool | Parameters | What it does |
|---|---|---|
| `concert_add` | `artist`, `start`, `venue`, `source_text`, `notes` | the whole concert flow: event on the Concerts calendar, teaser playlist, setlist.fm lookup, weekly queue, topic rename, reply delivery |
| `calendar_add` | `title`, `start`, `location`, `notes`, `calendar` | a plain event on the personal calendar, title kept as the user wrote it (`[Dinner] ...`) |
| `event_delete` | `title`, `date`, `pick` | delete one event by title; several matches become a numbered list, and `pick` (or a bare number as the title) chooses from it |
| `teaser_playlist` | `artist`, `date` | the Spotify teaser alone: every liked song by the artist, oldest first, named `YYYY-MM-DD \| Artist` |
| `setlist_find` | `artist`, `date`, `venue` | the setlist.fm page for one show: exact date first, then within a day at a venue sharing a word |
| `queue_status` | none | the concerts waiting for the weekly setlist check |
| `healthcheck` | none | which credentials and tools are present and what fixes each missing one |

`event_delete` carries `destructiveHint`; `setlist_find`, `queue_status` and `healthcheck` carry `readOnlyHint`.

## Getting started

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). `.python-version` pins 3.12, so `uv sync` installs and uses that interpreter whatever the machine has; CI also runs 3.11 and 3.13.

```sh
git clone https://github.com/AriSweedler/concert-mcp.git
cd concert-mcp
uv sync
uv run concert-mcp healthcheck
```

The healthcheck prints one row per check, a `->` line per failure naming the section of this README that fixes it, and exits with the number of FAIL rows. It is read-only: it creates no directory and writes no file. This is what it printed in an empty home directory on a Mac that has `gws` installed and nothing else configured (paths shortened):

```
PASS  python           3.12.12
PASS  mcp-sdk          mcp 2.3.0
PASS  config-dir       /Users/you/.config/concert-mcp absent (created on first write)
PASS  state-dir        /Users/you/.local/state/concert-mcp absent (created on first write)
PASS  gws-binary       /opt/homebrew/bin/gws
FAIL  gws-keyfile      missing
SKIP  gws-auth         pass --probe
SKIP  concerts-calendar pass --probe
FAIL  spotify-credentials missing
SKIP  spotify-auth     pass --probe
SKIP  liked-cache      absent: the first teaser will be slow (~40 s)
FAIL  setlistfm-key    missing
SKIP  setlistfm-auth   pass --probe
FAIL  openclaw-binary  openclaw not found
SKIP  openclaw-config  absent
SKIP  openclaw-session-db absent or unreadable: replies are not delivered
PASS  setlist-queue    0 shows queued
  ->  gws-keyfile      create the file keyring key: GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file gws auth login (README.md#google-calendar)
  ->  spotify-credentials concert-mcp spotify auth (README.md#spotify)
  ->  setlistfm-key    concert-mcp spotify setlist-auth (README.md#setlistfm)
  ->  openclaw-binary  install openclaw, or set CONCERT_MCP_OPENCLAW_BIN to its path (README.md#openclaw)
4 failing
```

`--probe` makes one real call per provider (list calendars, resolve the Concerts calendar, Spotify `/me`, one setlist.fm search) in place of the `pass --probe` rows. With `CONCERT_MCP_SEND=0` the three `openclaw-*` rows read `SKIP ... delivery off (CONCERT_MCP_SEND=0)`, which is how a development machine without OpenClaw gets to `3 failing`. When everything passes the last line is `all good`. `--json` prints the same report as one JSON object.

Nothing has to be configured to try the parser. A dry run touches no calendar and sends nothing:

```sh
uv run concert-mcp add --concert --dry-run --no-send --title 'ZZZ Test Band' --start '2026-11-20 20:00' --location 'ZZZ Hall'
```

```
**Would add to Concerts**
• **Artist:** ZZZ Test Band
• **When:** Fri Nov 20, 8:00 PM – 10:00 PM PST (end assumed)
• **Where:** ZZZ Hall
• **Teaser:** not made (Spotify is not set up: run `concert-mcp spotify auth`)
• **Setlist:** lookup skipped (setlist.fm is not set up: run `concert-mcp spotify setlist-auth`)
NOT SENT: dry run
```

## Configuration

Precedence: a `CONCERT_MCP_<FIELD>` environment variable wins over the same key, lowercase, in `<config_dir>/config.toml`, which wins over the default. The CLI flags `--config-dir PATH` and `--log-level LEVEL` set `CONCERT_MCP_CONFIG_DIR` and `CONCERT_MCP_LOG_LEVEL` for that run. The three directories honour `XDG_CONFIG_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME`. No default carries personal data: the operator name, calendar names, timezone and 1Password item names are defaults you override. This table is `config.env_table()`; `tests/test_readme.py` checks that every variable is here.

| Variable | Default | Meaning |
|---|---|---|
| `CONCERT_MCP_CONFIG_DIR` | `~/.config/concert-mcp` | credentials files and config.toml |
| `CONCERT_MCP_STATE_DIR` | `~/.local/state/concert-mcp` | setlist-queue.json, delete-candidates.json, last-sent.json, fix-log.json |
| `CONCERT_MCP_CACHE_DIR` | `~/.cache/concert-mcp` | liked.json |
| `CONCERT_MCP_SPOTIFY_CREDENTIALS` | `<config_dir>/spotify.json` | legacy fallback ~/.config/ari-spotify/credentials.json |
| `CONCERT_MCP_SETLISTFM_CREDENTIALS` | `<config_dir>/setlistfm.json` | legacy fallback ~/.config/ari-spotify/setlistfm.json |
| `CONCERT_MCP_LIKED_CACHE` | `<cache_dir>/liked.json` | legacy fallback ~/.cache/ari-spotify/liked.json |
| `CONCERT_MCP_SETLIST_QUEUE` | `<state_dir>/setlist-queue.json` | legacy fallback ~/.local/state/ari-spotify/setlist-queue.json; the weekly poller reads this file |
| `CONCERT_MCP_CANDIDATES_FILE` | `<state_dir>/delete-candidates.json` | no legacy fallback; a list older than `CONCERT_MCP_CONTEXT_WINDOW_S` is discarded |
| `CONCERT_MCP_SENT_LOG` | `<state_dir>/last-sent.json` | reply dedupe memory |
| `CONCERT_MCP_FIX_LOG` | `<state_dir>/fix-log.json` | the retry-loop breaker's memory |
| `CONCERT_MCP_GWS_BIN` | `which gws, else /opt/homebrew/bin, /usr/local/bin, ~/.local/bin, else gws` | the Google Workspace CLI |
| `CONCERT_MCP_GWS_KEY_FILE` | `~/.config/gws/.encryption_key` | exists -> GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file on every gws call |
| `CONCERT_MCP_GWS_TIMEOUT_S` | `60` | wall-clock cap per gws call |
| `CONCERT_MCP_OPENCLAW_BIN` | `which openclaw, else /opt/homebrew/bin, /usr/local/bin, ~/.local/bin, else openclaw` | the OpenClaw CLI |
| `CONCERT_MCP_OPENCLAW_HOME` | `~/.openclaw` | base for openclaw_config, session_db and ocr_last |
| `CONCERT_MCP_OPENCLAW_CONFIG` | `<openclaw_home>/openclaw.json` | read for channels.telegram.botToken only |
| `CONCERT_MCP_SESSION_DB` | `<openclaw_home>/agents/main/agent/openclaw-agent.sqlite` | opened read-only |
| `CONCERT_MCP_OCR_LAST` | `<openclaw_home>/media/ocr-last.txt` | the last OCR output |
| `CONCERT_MCP_TZ` | `America/Los_Angeles` | IANA zone for every start/end |
| `CONCERT_MCP_CONCERTS_CALENDAR` | `Concerts` | calendar name, resolved through gws calendar calendarList list |
| `CONCERT_MCP_DEFAULT_CALENDAR` | `primary` | id used for plain events and the second delete search |
| `CONCERT_MCP_DEFAULT_CALENDAR_NAME` | `Personal` | how replies name it |
| `CONCERT_MCP_CONTEXT_WINDOW_S` | `900` | live chat / OCR / transcript recency |
| `CONCERT_MCP_DEDUPE_WINDOW_S` | `600` | an identical reply is not sent twice within this window |
| `CONCERT_MCP_TEASER_TIMEOUT_S` | `150` | cap on the Spotify teaser step |
| `CONCERT_MCP_HTTP_TIMEOUT_S` | `30` | per HTTP request |
| `CONCERT_MCP_LIKED_CACHE_TTL_S` | `21600` | Liked Songs cache lifetime |
| `CONCERT_MCP_SETLIST_QUEUE_DAYS` | `61` | how long after the show the weekly setlist check keeps looking |
| `CONCERT_MCP_DEFAULT_DURATION_H` | `2` | end = start + this when no end is given |
| `CONCERT_MCP_PAST_GRACE_H` | `12` | a start this far in the past is still accepted |
| `CONCERT_MCP_MAX_FUTURE_DAYS` | `730` | a start further out is refused |
| `CONCERT_MCP_SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8888/callback` | the Spotify app's redirect URI |
| `CONCERT_MCP_SPOTIFY_SCOPES` | `user-library-read playlist-read-private playlist-modify-private playlist-modify-public` | OAuth scopes |
| `CONCERT_MCP_OP_VAULT` | `Private` | 1Password vault, auth commands only |
| `CONCERT_MCP_OP_SPOTIFY_ITEM` | `Spotify - concert-mcp` | fields username (client id), credential (secret), refresh token |
| `CONCERT_MCP_OP_SETLISTFM_ITEM` | `setlist.fm - API key` | field credential |
| `CONCERT_MCP_OPERATOR_NAME` | `Admin` | the name in 'Admin: check `gws auth` on the host.' |
| `CONCERT_MCP_SEND` | `1` | 0 disables reply delivery and topic renames everywhere |
| `CONCERT_MCP_RENAME_TOPICS` | `1` | 0 keeps Telegram topic names as they are |
| `CONCERT_MCP_LOG_LEVEL` | `info` | debug logs every subprocess argv (never tokens); stderr only |

Booleans accept `1/0`, `true/false`, `yes/no`, `on/off`. A `config.toml` sets the same fields without the prefix:

```toml
# ~/.config/concert-mcp/config.toml
operator_name = "your-name"
tz = "America/New_York"
concerts_calendar = "Shows"
default_calendar_name = "Home"
```

`concert-mcp config` prints the effective table, redacted: one row per field as `field  status  value`, where a path's status is `EXISTS`, `MISSING` or `LEGACY` (the ari-spotify file is in use because the new one is absent) and every other field shows `-`. No file's contents are ever read or printed:

```
config_dir               EXISTS   /Users/you/.config/concert-mcp
spotify_credentials      MISSING  /Users/you/.config/concert-mcp/spotify.json
gws_key_file             MISSING  /Users/you/.config/gws/.encryption_key
tz                       -        America/Los_Angeles
operator_name            -        Admin
send                     -        True
```

## Credentials

Four providers. Google Calendar is required. `concert_add` degrades without the other three (the reply says `not made (Spotify is not set up ...)`, `lookup skipped (setlist.fm is not set up ...)`, `NOT SENT: no live chat`), but the healthcheck expects every provider on the gateway host: a missing credential is a FAIL with its fix line and counts in the exit code, so the homelab doctor notices. The server reads the credential files lazily, so a `spotify auth` or `setlist-auth` run while it is up is picked up on the next call without a restart or `openclaw mcp reload`.

### Google Calendar

The package shells out to the [gws](https://github.com/googleworkspace/cli) CLI (Google Workspace CLI) and never handles Google credentials itself.

1. Install `gws` (Homebrew or its own install guide). On the host that will run the server, put the file keyring backend in the shell profile and log in once with it:

   ```sh
   echo 'export GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file' >> ~/.zprofile   # or ~/.profile, ~/.bashrc
   export GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file
   gws auth login
   ```

   `gws` creates `~/.config/gws/.encryption_key` itself (base64 of 32 random bytes, mode 600) and stores its credentials encrypted with it. When that file exists, every `gws` call from this package runs with `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file`, so headless runs (the gateway, a launchd job, plain `ssh`) never touch the macOS keychain. A key you made by hand (`head -c 32 /dev/urandom | base64 > ~/.config/gws/.encryption_key; chmod 600 ...`) is compatible but not needed.

   The export belongs in the profile because `gws` treats a successful keychain-mode login as a migration: it deletes `~/.config/gws/.encryption_key` and removes a `credentials.enc` it cannot decrypt. One plain `gws auth login`, or any `gws` command run without the variable on that host, therefore destroys the headless setup and the next tool call fails. The healthcheck's `gws-keyfile` row fails on a missing key file (`missing`, fix `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file gws auth login`) or one whose mode is not 600 (`mode 0o644, want 600`).
2. The keychain guard: without the file backend, on macOS, the package first runs `security show-keychain-info` on the login keychain and refuses to call `gws` at all when the keychain is unreachable, because `gws` would delete its credentials in that session. The reply is `The login keychain is locked in this session, so I did not touch Google Calendar. Admin: create the gws file keyring key (README.md#google-calendar) or run this from a session with keychain access.` and `healthcheck --probe` reports the same fix on its `gws-auth` row (its `concerts-calendar` row is then `SKIP gws-auth failed` rather than a second FAIL for the one fault).
3. Create a calendar named `Concerts` (or set `CONCERT_MCP_CONCERTS_CALENDAR`). The name is resolved through `gws calendar calendarList list`; a name matching zero or several calendars is a `FIX: use one of: ...` line. Plain events go to `primary`.

`gws calendar events delete` writes its empty response to a file in the current directory, so the package runs it in a fresh temporary directory with `-o gws-del.out` and removes both afterwards. `gws` stderr is keyring chatter and is never parsed.

### Spotify

Spotify is plain HTTPS through `urllib`, with the 2025 Web API (`POST /me/playlists`, playlist `items`).

1. Create an app at <https://developer.spotify.com/dashboard> with the redirect URI `http://127.0.0.1:8888/callback` (or whatever `CONCERT_MCP_SPOTIFY_REDIRECT_URI` says) and copy its Client ID and Client Secret. While the app is in development mode Spotify serves only the accounts listed under its User Management, so add the account that owns the playlists.
2. Run the one-time login on a machine with a browser, typing the two values hidden into the terminal:

   ```sh
   uv run concert-mcp spotify auth --manual
   ```

   Both must be 32 characters. A local HTTP server on the redirect URI waits up to 300 s for the consent redirect, checks the OAuth `state`, exchanges the code and writes the result.

   The optional backup path needs the 1Password CLI (`op`, signed in) and an item named `CONCERT_MCP_OP_SPOTIFY_ITEM` (default `Spotify - concert-mcp`, vault `Private`) whose `username` field holds the client id and `credential` the client secret; an item still holding `FILL_THIS_OUT` is refused. Then `uv run concert-mcp spotify auth` without `--manual` reads them with `op read`, and either form writes the refresh token back to the item's `refresh token` field, best effort, as the backup copy (never to the log, not even at `--log-level debug`). Without `op` the default form stops with `missing prerequisite | missing='op' hint='or pass --manual'`.
3. The credentials land in `~/.config/concert-mcp/spotify.json` (`CONCERT_MCP_SPOTIFY_CREDENTIALS`), mode 600 from the moment the file is created: client id, client secret, refresh token, the current access token and the Spotify user id. Every later command runs from that file without touching 1Password, so the bot never sees a 1Password prompt.

`auth` refuses to overwrite an existing file unless you pass `--force`. Liked Songs are cached in `~/.cache/concert-mcp/liked.json` for six hours; the first teaser after a cold cache takes about 40 s for a few thousand songs, and the whole teaser step is capped at `CONCERT_MCP_TEASER_TIMEOUT_S` (150 s). A 429 from Spotify is retried after its `Retry-After` (capped at 30 s); a 401 refreshes the token once.

### setlist.fm

1. Get a free API key at <https://www.setlist.fm/settings/api>.
2. Store it, typed hidden into the terminal:

   ```sh
   uv run concert-mcp spotify setlist-auth --manual
   ```

   The optional backup path, `uv run concert-mcp spotify setlist-auth` without `--manual`, needs the 1Password CLI (`op`) and reads the `credential` field of the item `CONCERT_MCP_OP_SETLISTFM_ITEM` (default `setlist.fm - API key`).

   The key (at least 20 characters, not the placeholder) is written to `~/.config/concert-mcp/setlistfm.json` (`CONCERT_MCP_SETLISTFM_CREDENTIALS`), mode 600, as `{"api_key": "..."}`, and one search is run to prove it works. `--force` replaces an existing key.
3. The API allows 2 requests per second and 1440 per day; the client waits 1, 2, 3 s on a 429, treats a 404 as "no match", and a concert lookup costs at most two requests (the exact date, then the year). Without a key the setlist bullet reads `lookup skipped (setlist.fm is not set up: run concert-mcp spotify setlist-auth)` and the event is still created and queued.

### OpenClaw

OpenClaw is the Telegram gateway. The package reads three things from it and shells out to it for one:

- `~/.openclaw/openclaw.json`: `channels.telegram.botToken`, read for the topic rename only (Bot API `editForumTopic`). The token is never logged, never put in a reply and never shown by the healthcheck, whose `openclaw-config` row reports only `telegram bot token present (topic rename possible)` or `SKIP no bot token: topics are not renamed`.
- `~/.openclaw/agents/main/agent/openclaw-agent.sqlite`: opened read-only to find the live chat (the newest `session_nodes` row with a delivery route and an interaction in the last 15 minutes) and the user's recent messages (`transcript_events`, newest 40), which join the ticket text the hallucination filter checks claims against.
- `~/.openclaw/media/ocr-last.txt`: the last OCR output, when it is less than 15 minutes old. Same purpose.
- `openclaw message send --channel telegram --target <chat> [--account <id>] [--thread-id <n>] -m <text>` delivers every reply (three tries of 20 s, four seconds between them). The model never copies a reply.

All three reads are best effort: when any of them fails the status is `NOT SENT: no live chat (CLI run)` and the model relays the reply itself, and with no source text every URL, price and clock time the caller supplied is dropped (the safe direction).

For the topic rename the bot must be an administrator of the Telegram group with the manage-topics right, and the request must come from a forum topic (a supergroup chat id starting with `-100` and a thread id other than `1`). Anything else yields `topic not renamed: not a Telegram forum topic`, or `topic not renamed: no live chat` from a CLI run; both are notes in the `--json` record (`topic_status`), not failures.

Every one of these paths is a setting (`CONCERT_MCP_OPENCLAW_HOME` moves all three). `CONCERT_MCP_SEND=0` turns delivery and renames off everywhere and makes the healthcheck skip the three `openclaw-*` rows, which is what the tests, a development machine and the CI healthcheck use.

## OpenClaw configuration

Register the server once on the gateway host; OpenClaw keeps a stdio server running and restarts it with backoff:

```sh
openclaw mcp set concert '{"command":"uv","args":["run","--project","/path/to/concert-mcp","concert-mcp","serve"],"env":{"CONCERT_MCP_OPERATOR_NAME":"${CONCERT_MCP_OPERATOR_NAME}","CONCERT_MCP_GWS_BIN":"${CONCERT_MCP_GWS_BIN}","CONCERT_MCP_OPENCLAW_BIN":"${CONCERT_MCP_OPENCLAW_BIN}"},"toolFilter":{"include":["concert_add","calendar_add","event_delete"]},"requestTimeoutMs":480000,"enabled":true}'
openclaw mcp doctor concert --probe     # starts the server once and lists its tools
openclaw mcp tools | grep concert__     # the three, as concert__concert_add and so on
openclaw mcp reload                     # after changing the entry, without restarting the gateway
```

The entry exposes three tools: `concert_add`, `calendar_add` and `event_delete`, the three jobs a chat message asks for. The other four (`teaser_playlist`, `setlist_find`, `queue_status`, `healthcheck`) are operator jobs the CLI already covers, and `concert_add` runs the first two itself; a 9B model given all seven is more likely to call one of them instead of `concert_add`. To expose them anyway, drop `toolFilter` from the entry: the server always registers all seven.

`requestTimeoutMs` is 480000 so that a slow provider never cuts a call off after the calendar insert and before the reply (the model would then retry; the duplicate guard makes that retry harmless, but the user waits twice and gets no link). A typical cold `concert_add` runs 60 to 150 s: a Liked Songs fetch is about 40 s, the teaser step is capped at `CONCERT_MCP_TEASER_TIMEOUT_S` (150 s, covering every Spotify read and write of the step), and `gws` and Telegram add a few seconds each. The worst case, with every provider at its cap, is about 8 minutes: teaser 150 s, two setlist.fm requests at 30 s, the duplicate check and the insert at 60 s each, three `openclaw message send` tries at 20 s with 4 s between them, and a 20 s topic rename. OpenClaw's default of 60 s is far too short. `${VAR}` in `env` expands from the gateway's environment; an unset variable expands to empty, which the package treats as "use the default". `CONCERT_MCP_GWS_BIN` and `CONCERT_MCP_OPENCLAW_BIN` are there for a gateway whose PATH has neither binary and whose install dir is not one of `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin` (those three are searched when PATH misses; a launchd-started gateway has a bare PATH).

What the model sees: the tool table above, each description under 300 characters and literal, and the server's `instructions` string, which OpenClaw injects into the system prompt (1104 characters, under OpenClaw's 2000-character cut). Its nine rules: call one tool once and never again unless the result starts with `FIX:`; a result starting `NO_REPLY` means the entire reply is exactly `NO_REPLY`; a `FIX:` means change only what it names and call once more, then ask the user in one sentence; any other result goes to the user unchanged, never with an invented link, time or price; a ticket goes to `concert_add` with the show time (not doors) and the whole text in `source_text`; a plain event goes to `calendar_add` with its `[Type]` prefix kept; delete or remove goes to `event_delete`, with `pick` when the user answers a numbered list; playlist alone, setlist alone, "what is queued" and "are you healthy" go to the four small tools; and never print a tool call as text.

## CLI usage

The same flows without an MCP client, for developers and for the dotfiles wrappers:

```
concert-mcp [--json] [--dry-run] [--no-send] [--config-dir PATH] [--log-level LEVEL] <subcommand>
```

| Subcommand | Example |
|---|---|
| `serve` | `concert-mcp serve` (the MCP server over stdio; also the default with no subcommand) |
| `add` | `concert-mcp add --concert --title 'Zed Fox with special guest Zork.' --start '2026-11-14 20:30' --location 'The Quarry, San Francisco' --source "$(cat ticket.txt)"` |
| `add` (plain) | `concert-mcp add --title '[Dinner] Team dinner' --start '2026-11-03 19:30' --location 'ZZZ Bistro, San Francisco'` |
| `del` (alias `delete`) | `concert-mcp del --title 'Zed Fox' --date 2026-11-14`, then `concert-mcp del --pick 2` after a numbered list (the list is good for 15 minutes, `CONCERT_MCP_CONTEXT_WINDOW_S`) |
| `teaser` | `concert-mcp teaser --artist 'Zed Fox' --date 2026-11-14 [--description TEXT]` |
| `setlist-find` | `concert-mcp setlist-find --artist 'Zed Fox' --date 2026-11-14 --venue 'The Quarry' --songs` (`--songs` adds the song list to the `--json` record) |
| `queue` | `concert-mcp queue` |
| `healthcheck` | `concert-mcp healthcheck --probe`, `concert-mcp healthcheck --only gws-keyfile --only spotify-credentials` |
| `config` | `concert-mcp config` |
| `migrate` | `concert-mcp migrate --dry-run`, then `concert-mcp migrate` |
| `spotify auth` | `concert-mcp spotify auth [--force] [--manual]` |
| `spotify me` | `concert-mcp spotify me` |
| `spotify liked` | `concert-mcp spotify liked --artist 'Zed Fox' [--refresh]` |
| `spotify playlists` | `concert-mcp spotify playlists` |
| `spotify playlist-tracks` | `concert-mcp spotify playlist-tracks --playlist <id>` |
| `spotify playlist-create` | `concert-mcp spotify playlist-create --name '2026-11-14 \| Zed Fox' [--description TEXT] [--public]` |
| `spotify playlist-add` | `concert-mcp spotify playlist-add --playlist <id> --uri spotify:track:<id> --uri spotify:track:<id>` |
| `spotify rename` | `concert-mcp spotify rename --playlist <id> --name '2026-11-14 \| Zed Fox'` |
| `spotify playlist-unfollow` | `concert-mcp spotify playlist-unfollow --playlist <id>` |
| `spotify search-artist` | `concert-mcp spotify search-artist --name 'Zed Fox'` |
| `spotify setlist-auth` | `concert-mcp spotify setlist-auth [--force] [--manual]` |

`add` and `del` also take one JSON object as the positional argument or on stdin, with the legacy aliases folded (`artist` and `summary` for `title`, `venue` for `location`, `notes` for `description`, `datetime` for `start`, `text` for `source`; `del` also takes `event`, `name`, `when`, `day`, `cal`, `eventId`, `number`, `choice`); an unknown key or flag is a `FIX:` line, never a traceback. `add` takes `--concert` (the Concerts calendar, teaser, setlist lookup, queue, rename) and `--no-teaser`. The `spotify` subcommands print one JSON record in the legacy shape.

Flags:

- `--json`: print the legacy record (`ok`, `duplicate`, `dryRun`, `retry` + `fix`, `error`, `reply`, the event fields and `sent`) instead of the reply text.
- `--dry-run`: parse, derive, resolve and render, but insert nothing, create no playlist, queue nothing and send nothing. The reply starts `**Would add to ...**`, and the status is `NOT SENT: dry run`.
- `--no-send`: do everything except deliver the reply and rename the topic; prints the reply plus `NOT SENT: delivery disabled`.
- `--config-dir PATH`, `--log-level LEVEL`: the two settings most often changed per run (`debug` logs every subprocess argv with any secret argument shown as `<redacted>`, never a token).

Exit codes: `0` for ok, including a duplicate and a dry run; `1` for a `FIX:` line, a reply that asks the user for something, or a provider failure (`130` on Ctrl-C); `healthcheck` exits with the number of FAIL rows. Logging goes to stderr only; stdout is exactly one block.

The dotfiles skills that used to carry `cal-add.py`, `cal-del.py` and `spotify.zsh` become one-line wrappers:

```sh
exec concert-mcp add --concert "$@"    # cal-add.py
exec concert-mcp del "$@"              # cal-del.py
exec concert-mcp spotify "$@"          # spotify.zsh (teaser and setlist-find are top-level: concert-mcp teaser, concert-mcp setlist-find)
```

## Development

```sh
uv sync --dev
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

Tests touch no network, no keychain, no home directory, no `gws` and no `openclaw`: the pure modules are tested against literal strings copied from the legacy scripts, the flows run end to end on `tests/fakes.py`, and the adapters run on a fake runner and a fake `urlopen`. `conftest.py` points every `CONCERT_MCP_*_DIR` at `tmp_path` and sets `CONCERT_MCP_SEND=0`. `tests/test_readme.py` checks this file against the code: every healthcheck anchor is a heading here, every setting is in the table, the OpenClaw snippet parses with a long enough timeout, and no personal data is in any text file of the repository.

The stdio smoke test spawns the real server (`python -m concert_mcp serve`) through the MCP client, checks the instructions and the seven tool names, and calls `healthcheck`:

```sh
uv run pytest -q tests/test_smoke_stdio.py
```

A parity test against the legacy `cal-add.py`, gated by `CONCERT_MCP_LEGACY_DIR` naming a directory that holds it, is specified in [docs/DESIGN.md](docs/DESIGN.md) §7 (`tests/test_legacy_parity.py`) and is not written yet; the goldens in `tests/` carry the legacy strings until it is.

CI runs lint, the tests on Python 3.11, 3.12 and 3.13, the healthcheck on a bare runner (it must exit 3 or more and point at this README) and the personal-data scan alone (`tests/test_readme.py`, which reads every text file in the repository for email addresses, Telegram chat ids and bot tokens, Google calendar ids and `.local` hostnames; the one pattern list lives in that test).

Release: bump `__version__` in `src/concert_mcp/__init__.py` and `version` in `pyproject.toml`, move the `Unreleased` entries in `CHANGELOG.md` under the new version with today's date, commit, and tag `vX.Y.Z`.

## Design principles

**The small-model rule.** The model behind the bot is a 9B model on a small GPU. Models that size fail with many tools, drop chained calls and print tool JSON as text, so: few coarse tools (one per job, with the plan inside the tool); every parameter a string with an example in its description, never a required field in the schema (a missing one yields a validation blob the model loops on; code returns the `I could not read the ... from that` reply instead); code owns policy, defaults, validation, idempotency, side effects and the reply text. `clean_param` squashes whitespace, strips one pair of matching quotes and turns `null`, `None`, `n/a` and `undefined` into empty.

**Three result shapes.** The tool result tells the model exactly what to say: the `NO_REPLY` block when code delivered the reply itself, or the reply text followed by `NOT SENT: <why>` for the model to relay, or `FIX: <one change>`. `FIX` is the only retry signal, and `fix_guard` turns the third FIX for one tool within 15 minutes into a plain question to the user (`I could not get the details right from that after a few tries. Reply with the artist, date, show time and venue in one line and I will add it.`), so a confused model cannot loop. A crash inside a tool is a reply too: `Something broke inside the concert tool (<Type>). Admin: check the gateway log.`

**The hallucination filter.** Doors, Purchased and Opener lines are derived from the ticket text by code, never taken from the model. A caller-supplied description line is kept only when every URL, dollar amount and clock time in it appears in the source text (the ticket text passed in, the last OCR output and the user's recent messages); otherwise it is dropped and the reply says so: `Left out (not in the text I was given): ...`. A start equal to the doors time is refused with a literal FIX (`set start to "2026-11-14 8:30 PM" (the show time in the text; 7:30 PM is doors)`); a date-only start takes the show time from the text. Only a concert runs these parsers: a plain `calendar_add` event keeps its title and start as given, and the context text (which belongs to the last ticket) is used only to verify the caller's own lines.

**The duplicate guard.** Before inserting, the flow lists events at the exact start minute with the same title; a match means nothing is created, missing playlist and setlist links are patched into the existing description, and the reply reads `**Already on Concerts** (nothing new created)`. A model retry after a timeout is therefore harmless. The setlist queue, the delete candidates list and the sent log are all idempotent by key and written atomically.

**One link form.** The Google Calendar link in every reply is `https://calendar.google.com/calendar/u/0/r/eventedit/<eid>`, built from the `eid` in the API's `htmlLink`. Four link forms were tested on a phone on 2026-10-25 (the API's `htmlLink`, the `calendar.google.com/calendar/event?eid=` form, the day view and the share template): only the `eventedit` form opened the event in the Google Calendar app. There is no fallback form.

**Stdout discipline.** Stdout is the MCP transport. Nothing in `src/` calls `print()` except `cli.py` and the interactive `flows/auth.py`; a test greps for it. All logging goes to stderr.

**Configuration carries no personal data.** The operator's name, calendar names, timezone and 1Password item names are settings with neutral defaults; replies say `Admin: ...` until you set `CONCERT_MCP_OPERATOR_NAME`. The healthcheck prints a calendar's name, never its id, and reports a bot token's presence, never its value.

## Migration from ari-spotify

If the `ari-spotify` dotfiles skill ran on this host, its four files are read where they are until the new paths exist, and one command copies them (never deletes, mode 600 for the two credential files):

```sh
uv run concert-mcp migrate
```

The weekly setlist poller must be repointed at the new queue path at the same time. Details, the path table and what the dotfiles wrappers become: [docs/migration.md](docs/migration.md).

## License

MIT. See [LICENSE](LICENSE).
