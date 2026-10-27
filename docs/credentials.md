# Credentials

Four providers. Google Calendar is required. `concert_add` degrades without the other three (the reply says `not made (Spotify is not set up ...)`, `lookup skipped (setlist.fm is not set up ...)`, `NOT SENT: no live chat`), but the healthcheck expects every provider on the gateway host: a missing credential is a FAIL with its fix line and counts in the exit code, so the homelab doctor notices. The server reads the credential files lazily, so a `spotify auth` or `setlist-auth` run while it is up is picked up on the next call without a restart or `openclaw mcp reload`.

## Google Calendar

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

## Spotify

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

## setlist.fm

1. Get a free API key at <https://www.setlist.fm/settings/api>.
2. Store it, typed hidden into the terminal:

   ```sh
   uv run concert-mcp spotify setlist-auth --manual
   ```

   The optional backup path, `uv run concert-mcp spotify setlist-auth` without `--manual`, needs the 1Password CLI (`op`) and reads the `credential` field of the item `CONCERT_MCP_OP_SETLISTFM_ITEM` (default `setlist.fm - API key`).

   The key (at least 20 characters, not the placeholder) is written to `~/.config/concert-mcp/setlistfm.json` (`CONCERT_MCP_SETLISTFM_CREDENTIALS`), mode 600, as `{"api_key": "..."}`, and one search is run to prove it works. `--force` replaces an existing key.
3. The API allows 2 requests per second and 1440 per day; the client waits 1, 2, 3 s on a 429, treats a 404 as "no match", and a concert lookup costs at most two requests (the exact date, then the year). Without a key the setlist bullet reads `lookup skipped (setlist.fm is not set up: run concert-mcp spotify setlist-auth)` and the event is still created and queued.

## OpenClaw

OpenClaw is the Telegram gateway. The package reads three things from it and shells out to it for one:

- `~/.openclaw/openclaw.json`: `channels.telegram.botToken`, read for the topic rename only (Bot API `editForumTopic`). The token is never logged, never put in a reply and never shown by the healthcheck, whose `openclaw-config` row reports only `telegram bot token present (topic rename possible)` or `SKIP no bot token: topics are not renamed`.
- `~/.openclaw/agents/main/agent/openclaw-agent.sqlite`: opened read-only to find the live chat (the newest `session_nodes` row with a delivery route and an interaction in the last 15 minutes) and the user's recent messages (`transcript_events`, newest 40), which join the ticket text the hallucination filter checks claims against.
- `~/.openclaw/media/ocr-last.txt`: the last OCR output, when it is less than 15 minutes old. Same purpose.
- `openclaw message send --channel telegram --target <chat> [--account <id>] [--thread-id <n>] -m <text>` delivers every reply (three tries of 20 s, four seconds between them). The model never copies a reply.

All three reads are best effort: when any of them fails the status is `NOT SENT: no live chat (CLI run)` and the model relays the reply itself, and with no source text every URL, price and clock time the caller supplied is dropped (the safe direction).

For the topic rename the bot must be an administrator of the Telegram group with the manage-topics right, and the request must come from a forum topic (a supergroup chat id starting with `-100` and a thread id other than `1`). Anything else yields `topic not renamed: not a Telegram forum topic`, or `topic not renamed: no live chat` from a CLI run; both are notes in the `--json` record (`topic_status`), not failures.

Every one of these paths is a setting (`CONCERT_MCP_OPENCLAW_HOME` moves all three). `CONCERT_MCP_SEND=0` turns delivery and renames off everywhere and makes the healthcheck skip the three `openclaw-*` rows, which is what the tests, a development machine and the CI healthcheck use.

## Venues (optional)

Every concert's venue is looked up once and kept in `~/.config/concert-mcp/venues.json`: the Maps-form address that Google Calendar turns into a place card, the website, and the dates visited. Nothing to set up: without a key the lookup goes to OpenStreetMap's Nominatim (free, one request per new venue, no account), and when that finds nothing the address block printed on the ticket is used.

A Google Places API key makes the lookup more exact and adds the website more often. Create a key in Google Cloud with the Places API (New) enabled, then store it:

```sh
concert-mcp venues auth --manual      # type the key hidden; or without --manual, read it from 1Password
concert-mcp venues                    # the registry
concert-mcp venues lookup --name 'Cafe Du Nord'
```

The key file is `<config_dir>/places.json`, mode 600. The address fields bill at the Pro SKU (5,000 free calls a month) and the website field at Enterprise (1,000 free); a few venues a month costs nothing. `CONCERT_MCP_VENUE_LOOKUP=0` turns every network lookup off.

## Registering the server with OpenClaw

Register the server once on the gateway host; OpenClaw keeps a stdio server running and restarts it with backoff:

```sh
openclaw mcp set concert '{"command":"uv","args":["run","--project","/path/to/concert-mcp","concert-mcp","serve"],"env":{"CONCERT_MCP_OPERATOR_NAME":"${CONCERT_MCP_OPERATOR_NAME}","CONCERT_MCP_GWS_BIN":"${CONCERT_MCP_GWS_BIN}","CONCERT_MCP_OPENCLAW_BIN":"${CONCERT_MCP_OPENCLAW_BIN}"},"toolFilter":{"include":["concert_add","calendar_add","event_delete"]},"requestTimeoutMs":480000,"enabled":true}'
openclaw mcp doctor concert --probe     # starts the server once and lists its tools
openclaw mcp probe concert              # the three, as concert__concert_add and so on
openclaw config set tools.alsoAllow '["exec","read","concert__concert_add","concert__calendar_add","concert__event_delete"]'
openclaw mcp reload                     # after changing the entry, without restarting the gateway
```

The `tools.alsoAllow` line matters when the agent runs the `minimal` tool profile (the usual setup for a small local model): that profile hides every tool it does not name, MCP tools included, and the model then answers "I don't have a calendar tool". Keep whatever the list already holds and add the three `concert__` names; `openclaw config get tools.alsoAllow` shows it.

The entry exposes three tools: `concert_add`, `calendar_add` and `event_delete`, the three jobs a chat message asks for. The other four (`teaser_playlist`, `setlist_find`, `queue_status`, `healthcheck`) are operator jobs the CLI already covers, and `concert_add` runs the first two itself; a 9B model given all eight is more likely to call one of them instead of `concert_add`. `setlist_playlist` (a pasted setlist.fm link) is a chat job and belongs in the filter. To expose the rest anyway, drop `toolFilter` from the entry: the server always registers all eight.

`requestTimeoutMs` is 480000 so that a slow provider never cuts a call off after the calendar insert and before the reply (the model would then retry; the duplicate guard makes that retry harmless, but the user waits twice and gets no link). A typical cold `concert_add` runs 60 to 150 s: a Liked Songs fetch is about 40 s, the teaser step is capped at `CONCERT_MCP_TEASER_TIMEOUT_S` (150 s, covering every Spotify read and write of the step), and `gws` and Telegram add a few seconds each. The worst case, with every provider at its cap, is about 8 minutes: teaser 150 s, two setlist.fm requests at 30 s, the duplicate check and the insert at 60 s each, three `openclaw message send` tries at 20 s with 4 s between them, and a 20 s topic rename. OpenClaw's default of 60 s is far too short. `${VAR}` in `env` expands from the gateway's environment; an unset variable expands to empty, which the package treats as "use the default". `CONCERT_MCP_GWS_BIN` and `CONCERT_MCP_OPENCLAW_BIN` are there for a gateway whose PATH has neither binary and whose install dir is not one of `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin` (those three are searched when PATH misses; a launchd-started gateway has a bare PATH).

What the model sees: the tool table above, each description under 300 characters and literal, and the server's `instructions` string, which OpenClaw injects into the system prompt (1104 characters, under OpenClaw's 2000-character cut). Its nine rules: call one tool once and never again unless the result starts with `FIX:`; a result starting `NO_REPLY` means the entire reply is exactly `NO_REPLY`; a `FIX:` means change only what it names and call once more, then ask the user in one sentence; any other result goes to the user unchanged, never with an invented link, time or price; a ticket goes to `concert_add` with the show time (not doors) and the whole text in `source_text`; a plain event goes to `calendar_add` with its `[Type]` prefix kept; delete or remove goes to `event_delete`, with `pick` when the user answers a numbered list; playlist alone, setlist alone, "what is queued" and "are you healthy" go to the four small tools; and never print a tool call as text.
